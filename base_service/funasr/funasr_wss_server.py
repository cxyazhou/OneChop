import asyncio
import json
import websockets
import argparse
import sys
import os
import threading
from time import sleep
import concurrent.futures

# 将项目根目录添加到 Python 路径
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import ConfigManager

# 创建配置管理器实例
config = ConfigManager()

server_loop = None
server_thread = None
server_tasks = []
ws_server = None
websocket_users = set()

model_asr = None
model_punc = None

cur_dir = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser()
parser.add_argument(
    "--host", type=str, default=config.get("funasr", "host"), required=False, help="host ip, localhost, 0.0.0.0"
)
parser.add_argument("--port", type=int, default=config.get("funasr", "port"), required=False, help="grpc server port")  

asr_model_path = "./model/asr_model/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1"
if not os.path.exists(asr_model_path):
    asr_model_path = os.path.join(cur_dir, "model/asr_model/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1")

parser.add_argument(
    "--asr_model",
    type=str,
    default=asr_model_path,
    help="model from modelscope",
)
parser.add_argument("--asr_model_revision", type=str, default="v2.0.4", help="")
parser.add_argument(
    "--punc_model",
    type=str,
    default="",
    help="model from modelscope",
)
parser.add_argument("--punc_model_revision", type=str, default="v2.0.4", help="")
parser.add_argument("--ngpu", type=int, default=1, help="0 for cpu, 1 for gpu")
parser.add_argument("--device", type=str, default="cuda", help="cuda, cpu")
parser.add_argument("--ncpu", type=int, default=4, help="cpu cores")

args = parser.parse_args()

def model_load():
    global model_asr, model_punc
    
    print("model loading")
    # 导入比较耗时，放在需要使用时再导入
    try:
        from funasr import AutoModel
    except ImportError:
        print("错误：funasr 模块未安装")
        return

    # asr
    model_asr = AutoModel(
        model=args.asr_model,
        model_revision=args.asr_model_revision,
        ngpu=args.ngpu,
        ncpu=args.ncpu,
        device=args.device,
        disable_pbar=True,
        disable_log=True,
        disable_update=True,
    )

    if args.punc_model != "":
        model_punc = AutoModel(
            model=args.punc_model,
            model_revision=args.punc_model_revision,
            ngpu=args.ngpu,
            ncpu=args.ncpu,
            device=args.device,
            disable_pbar=True,
            disable_log=True,
            disable_update=True,
        )

    print("model loaded!")

async def ws_reset(websocket):
    websocket.status_dict_punc["cache"] = {}

    await websocket.close()

async def clear_websocket():
    global websocket_users

    for websocket in websocket_users:
        await ws_reset(websocket)
    websocket_users.clear()

async def ws_serve(websocket):
    global websocket_users
    frames_asr = []
    await clear_websocket()
    websocket_users.add(websocket)
    websocket.status_dict_asr = {}
    websocket.status_dict_punc = {"cache": {}}
    speech_start = False
    websocket.wav_name = "microphone"
    websocket.mode = "2pass"

    try:
        async for message in websocket:
            if isinstance(message, str):
                messagejson = json.loads(message)

                # print(messagejson)

                if "is_speaking" in messagejson:
                    speech_start = messagejson["is_speaking"]
                    # print(speech_start)
                if "wav_name" in messagejson:
                    websocket.wav_name = messagejson.get("wav_name")
                if "hotwords" in messagejson:
                    websocket.status_dict_asr["hotword"] = messagejson["hotwords"]
                if "mode" in messagejson:
                    websocket.mode = messagejson["mode"]

            if speech_start:
                if not isinstance(message, str):
                    # print("put data")
                    frames_asr.append(message)

            else:
                audio_in = b"".join(frames_asr)
                try:
                    # print(f"处理音频数据，长度：{len(audio_in)} 字节")
                    await async_asr(websocket, audio_in)
                except:
                    # print("error in asr offline")
                    mode = "2pass-offline" if "2pass" in websocket.mode else websocket.mode
                    message = json.dumps(
                        {
                            "mode": mode,
                            "text": "",
                            "wav_name": websocket.wav_name,
                        }
                    )
                    await websocket.send(message)
                frames_asr = []

    except websockets.ConnectionClosed:
        print("ConnectionClosed...", websocket_users, flush=True)
        await ws_reset(websocket)
        websocket_users.remove(websocket)
    except websockets.InvalidState:
        print("InvalidState...")
    except Exception as e:
        print("Exception:", e)

async def async_asr(websocket, audio_in):
    global model_asr, model_punc

    if len(audio_in) > 0:
        # print(f"audio_in len: {len(audio_in)}")
        try:
            # print(f"开始识别，模型：{model_asr}")
            rec_result = model_asr.generate(input=audio_in, **websocket.status_dict_asr)[0]
            # print(f"offline_asr, {rec_result}")
            
            if model_punc is not None and len(rec_result["text"]) > 0:
                # print("offline, before punc", rec_result, "cache", websocket.status_dict_punc)
                rec_result = model_punc.generate(
                    input=rec_result["text"], **websocket.status_dict_punc
                )[0]
                # print("offline, after punc", rec_result)
                
            if len(rec_result["text"]) > 0:
                # print("offline", rec_result)
                mode = "2pass-offline" if "2pass" in websocket.mode else websocket.mode
                message = json.dumps(
                    {
                        "mode": mode,
                        "text": rec_result["text"],
                        "wav_name": websocket.wav_name,
                    }
                )
                # print(message)
                await websocket.send(message)

            else:
                mode = "2pass-offline" if "2pass" in websocket.mode else websocket.mode
                message = json.dumps(
                    {
                        "mode": mode,
                        "text": "",
                        "wav_name": websocket.wav_name,
                    }
                )
                await websocket.send(message)
                
        except Exception as e:
            print(f"识别过程中出错：{e}")
            import traceback
            traceback.print_exc()

    else:
        mode = "2pass-offline" if "2pass" in websocket.mode else websocket.mode
        message = json.dumps(
            {
                "mode": mode,
                "text": "",
                "wav_name": websocket.wav_name,
            }
        )
        await websocket.send(message)

def funasr_server_start():
    global server_thread
    
    def run_server():
        global server_loop, server_tasks
        
        # 1. 确保在创建 loop 之前设置策略 (Windows 特有)
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            
        server_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(server_loop)

        async def start_server_async():
            global ws_server, server_tasks

            try:
                model_load()
                ws_server = await websockets.serve(
                    ws_serve, args.host, args.port, subprotocols=None, ping_interval=None
                )
                print(f"FunASR 服务器已启动，监听 {args.host}:{args.port}")
                
                # 创建一个任务来等待服务器关闭
                server_task = asyncio.create_task(ws_server.wait_closed())
                server_tasks.append(server_task)
                
                await server_task  # 等待服务器关闭
            except asyncio.CancelledError:
                print("服务器任务被取消")
                # 无需手动关闭 ws_server，因为 wait_closed 已被取消
                raise
            except Exception as e:
                print(f"服务器运行出错：{e}")
                raise

        try:
            server_loop.run_until_complete(start_server_async())
        except asyncio.CancelledError:
            pass # 预期内的取消
        except RuntimeError as e:
            # 只有在 loop 已经停止的情况下再次运行才会报这个，通常忽略
            if "Event loop stopped before Future completed" not in str(e):
                print(f"事件循环异常：{e}")
        finally:
            # 关键清理步骤：在 loop 关闭前，取消所有剩余任务
            cleanup_loop(server_loop)
            # 不要在这里关闭 loop，让调用者或线程结束时处理，或者显式关闭
            # server_loop.close() 

    server_thread = threading.Thread(target=run_server, daemon=False)
    server_thread.start()

def cleanup_loop(loop):
    """取消所有 pending 任务并等待它们完成，防止 Task destroyed 警告"""
    if not loop:
        return
    
    try:
        # 获取所有正在运行的任务
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        
        if not tasks:
            return
        
        # 取消所有任务
        for task in tasks:
            task.cancel()
        
        # 等待所有任务处理 CancelledError 并完成
        # 注意：这需要在 loop 运行时执行，所以我们要临时跑一下
        if loop.is_running():
            # 如果 loop 还在跑（比如通过 call_soon_threadsafe 触发清理），不能直接 run_until_complete
            # 这种情况下，通常由主协程处理。这里主要处理 loop 刚停止但任务还在的情况。
            pass
        else:
            # 如果 loop 还没完全关掉，或者我们重新启动它来清理（不推荐复杂化）
            # 最简单的做法是：在 run_server 的 finally 块里，如果 loop 没关，手动跑一下清理协程
            async def gather_cancelled():
                await asyncio.gather(*tasks, return_exceptions=True)
            
            # 如果 loop 已经 stopped，我们无法 run_until_complete。
            # 最佳实践是在 stop 信号传来时，在主 async 函数内处理 cancel。
            # 鉴于当前架构，我们在 stop 函数中通过 thread_safe 方式注入清理逻辑。
            pass
            
    except Exception as e:
        print(f"清理任务时出错：{e}")

def funasr_server_stop():
    global ws_server, server_loop, server_thread, server_tasks
    print("正在关闭 FunASR 服务器...")

    if server_loop is None:
        print("服务器未启动或已关闭")
        return

    # 定义一个在事件循环内部运行的清理协程
    async def shutdown_procedure():
        global ws_server, server_tasks
        
        # 1. 关闭 WebSocket 服务器
        if ws_server:
            ws_server.close()
            await ws_server.wait_closed()
            ws_server = None
        
        # 2. 取消所有我们跟踪的任务
        tasks_to_cancel = [t for t in server_tasks if not t.done()]
        if tasks_to_cancel:            
            # 分批取消和等待
            batch_size = 50
            for i in range(0, len(tasks_to_cancel), batch_size):
                batch = tasks_to_cancel[i:i+batch_size]
                for t in batch:
                    t.cancel()
                
                # 等待当前批次完成
                try:
                    await asyncio.wait(batch, return_when=asyncio.ALL_COMPLETED)
                except RecursionError:
                    # 如果仍然出错，尝试更小的批次
                    print("递归错误，减小批次大小")
                    batch_size = max(1, batch_size // 2)
                    for t in batch:
                        t.cancel()
                    await asyncio.wait(batch, return_when=asyncio.ALL_COMPLETED)
        
        # 清理任务列表
        server_tasks = [t for t in server_tasks if t.done()]

    try:
        # 将关闭逻辑投递到事件循环线程执行
        future = asyncio.run_coroutine_threadsafe(shutdown_procedure(), server_loop)
        future.result(timeout=2)
    except concurrent.futures.TimeoutError:
        print("关闭操作超时，强制终止中...")
        # 如果超时，尝试强制停止事件循环
        if server_loop.is_running():
            server_loop.call_soon_threadsafe(server_loop.stop)
    except Exception as e:
        print(f"关闭过程中发生错误：{e}")

    # 等待服务器线程退出
    if server_thread is not None:
        try:
            server_thread.join(timeout=5)
            if server_thread.is_alive():
                print("警告：服务器线程仍未退出。")
            else:
                pass
        except Exception as e:
            print(f"等待线程结束时出错：{e}")

    # 清理全局变量
    if server_loop:
        try:
            server_loop.close()
        except Exception:
            pass
            
    ws_server = None
    server_loop = None
    server_thread = None
    server_tasks = []  # 清理任务列表

    print("FunASR 服务器已关闭")

def main():
    funasr_server_start()
    while True:
        try:
            sleep(1)
        except KeyboardInterrupt:
            break

    funasr_server_stop()

if __name__ == "__main__":
    main()