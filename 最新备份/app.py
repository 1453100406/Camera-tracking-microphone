# File: app.py
import time
import threading
import requests
import serial
import urllib3
import webbrowser
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import webview
from random import choice

import sys
import logging

# 创建日志记录器
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    handlers=[
        logging.FileHandler("app.log", mode='w', encoding='utf-8'),  # 写入 app.log 文件
        logging.StreamHandler(sys.stdout)  # 同时输出到 stdout（调试时也能看见）
    ]
)

# 将 print 重定向到 logging.info
print = logging.info

# 忽略 HTTPS 自签名证书验证警告（用于 D-Cerno API）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = Flask(__name__)
CORS(app)

#追踪
is_tracking_enabled = False

# -------- D-Cerno Customer API 配置 --------
DCERNO_BASE = 'https://192.168.0.20:9443'
CAMERA_API_KEY = '26cb2a7e-360e-4abd-a3ef-62808139d444'
MIC_API_KEY = '26cb2a7e-360e-4abd-a3ef-62808139d444'

CAMERA_HEADERS = {
    'Authorization': f'Bearer {CAMERA_API_KEY}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

MIC_HEADERS = {
    'Authorization': f'Bearer {MIC_API_KEY}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# -------- 串口配置：连接 Cisco Codec EQ --------
SERIAL_PORT = 'COM6'
BAUDRATE = 115200
TIMEOUT = 1.0
PAN_RATE = 30.0
TILT_RATE = 30.0
ZOOM_RATE = 30.0

serial_enabled = True
try:
    codec_serial = serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=TIMEOUT,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False
    )
    print(f"✅ 串口已打开：{SERIAL_PORT}（{BAUDRATE}bps）")
except Exception as e:
    print(f"[Error] 无法打开串口 {SERIAL_PORT}: {e}，摄像头控制已禁用")
    serial_enabled = False
    codec_serial = None

# -------- 摄像头状态管理 --------
current_cam = '1'
cam_state = {
    '1': {'pan': 0, 'tilt': 0, 'zoom': 100},
    '2': {'pan': 0, 'tilt': 0, 'zoom': 100},
}

def send_camera_command(pan: int, tilt: int, zoom: int):
    if not serial_enabled:
        print("[Camera] 串口未启用，跳过发送")
        return
    old = cam_state[current_cam]

    # Pan 控制
    delta_pan = pan - old['pan']
    if delta_pan != 0:
        direction = 'Right' if delta_pan > 0 else 'Left'
        duration = abs(delta_pan) / PAN_RATE
        print(f"[Camera] Ramp Pan {direction} for {duration:.2f}s")
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Pan: {direction}\r\n".encode())
        time.sleep(duration)
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Pan: Stop\r\n".encode())
        cam_state[current_cam]['pan'] = pan
        #time.sleep(0.1)

    # Tilt 控制
    delta_tilt = tilt - old['tilt']
    if delta_tilt != 0:
        direction = 'Up' if delta_tilt > 0 else 'Down'
        duration = abs(delta_tilt) / TILT_RATE
        print(f"[Camera] Ramp Tilt {direction} for {duration:.2f}s")
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Tilt: {direction}\r\n".encode())
        time.sleep(duration)
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Tilt: Stop\r\n".encode())
        cam_state[current_cam]['tilt'] = tilt
        #time.sleep(0.1)

    # Zoom 控制
    delta_zoom = zoom - old['zoom']
    if delta_zoom != 0:
        action = 'In' if delta_zoom > 0 else 'Out'
        duration = abs(delta_zoom) / ZOOM_RATE
        print(f"[Camera] Ramp Zoom {action} for {duration:.2f}s")
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Zoom: {action}\r\n".encode())
        time.sleep(0.25)
        codec_serial.write(f"xCommand Camera Ramp CameraId: {current_cam} Zoom: Stop\r\n".encode())
        cam_state[current_cam]['zoom'] = zoom
        #time.sleep(0.1)

    print(f"[Camera] New state: {cam_state[current_cam]}")

# -------- Camera 页面及接口 --------
@app.route('/')
@app.route('/camera')
def camera_page():
    return render_template('Camera.html')

@app.route('/api/tracking/status', methods=['GET'])
def get_tracking_status():
    return jsonify({'tracking': is_tracking_enabled})


# def camera_page():
#     return render_template('Camera.html')

#追踪
@app.route('/api/tracking/enable', methods=['POST'])
def toggle_tracking():
    global is_tracking_enabled
    data = request.json or {}
    enabled = data.get('enabled', False)
    is_tracking_enabled = bool(enabled)
    return jsonify({'tracking': is_tracking_enabled})


@app.route('/api/cam/select', methods=['POST'])
def cam_select():
    global current_cam
    cam = str(request.json.get('cam', '')).strip()
    if cam not in cam_state:
        return jsonify({'error': 'cam must be "1" or "2"'}), 400
    current_cam = cam
    return jsonify({'selected': current_cam}), 200

@app.route('/api/cam/ptz', methods=['POST'])
def cam_ptz():
    data = request.json or {}
    pan = int(data.get('pan', cam_state[current_cam]['pan']))
    tilt = int(data.get('tilt', cam_state[current_cam]['tilt']))
    zoom = int(data.get('zoom', cam_state[current_cam]['zoom']))
    send_camera_command(pan, tilt, zoom)
    return jsonify({'cam': current_cam, 'pan': pan, 'tilt': tilt, 'zoom': zoom})

@app.route('/api/cam/zoom', methods=['POST'])
def cam_zoom():
    action = request.json.get('action')
    prev = cam_state[current_cam]
    delta = 50 if action == 'in' else -50
    newz = max(25, min(500, prev['zoom'] + delta))
    send_camera_command(prev['pan'], prev['tilt'], newz)
    return jsonify({'cam': current_cam, 'pan': prev['pan'], 'tilt': prev['tilt'], 'zoom': newz})

@app.route('/api/cam/get', methods=['GET'])
def cam_get():
    return jsonify(cam_state)

@app.route('/api/cam/preset/activate', methods=['POST'])
def activate_preset():
    cameraId=2
    data = request.json or {}
    if 'seat' in data:
        try:
            seat_num = int(data['seat'].replace('seat', ''))
            #preset_id = seat_num + 20
            preset_id = seat_num + 20
        except:
            return jsonify({'error': 'seat 参数格式错误'}), 400
    elif 'presetId' in data:
        try:
            preset_id = int(data['presetId'])
        except:
            return jsonify({'error': 'invalid presetId'}), 400
    else:
        return jsonify({'error': '缺少 seat 或 presetId'}), 400
    if not serial_enabled:
        return jsonify({'error': 'serial disabled'}), 500
    
    #查询当前显示源
    #xStatus Video Input MainVideoSource
    if preset_id >25:
        cameraId = 1
    else:
        cameraId = 2
        
    if preset_id!=32:
        cmd = (
            f"xCommand Video Input setMainVideoSource SourceId: {cameraId}\r\n"
            f"xCommand Camera Preset Activate PresetId: {preset_id}\r\n"
        )
        print(f"[Serial →] {cmd.strip()}")  # ← 打印完整命令

        codec_serial.write(cmd.encode()); codec_serial.flush()
        return jsonify({'status': 'ok', 'presetId': preset_id}), 200
    
    cmd = (
            "xCommand Video Input setMainVideoSource SourceId: 1\r\n"
            "xCommand Camera Preset Activate PresetId: 14\r\n"
        )
    print(f"[Serial →] {cmd.strip()}")  # ← 打印完整命令
    codec_serial.write(cmd.encode()); codec_serial.flush()
    return jsonify({'status': 'ok', 'presetId': preset_id}), 200

@app.route('/api/cam/preset/save', methods=['POST'])
def save_preset():
    data = request.json or {}
    seat = data.get('seat')
    if not seat or not seat.startswith('seat'):
        return jsonify({'error': '无效 seat'}), 400
    try:
        seat_num = int(seat.replace('seat', ''))
        preset_id = seat_num + 20
        camera_id = 1 if seat == 'seat6' or seat == 'seat7' or seat == 'seat8' or seat == 'seat9' or seat == 'seat10' or seat == 'seat11'else 2
    except:
        return jsonify({'error': 'seat 参数格式错误'}), 400
    if not serial_enabled:
        return jsonify({'error': 'serial disabled'}), 500
    cmd = (
        f"xCommand Camera Preset Store CameraId: {camera_id} "
        f"PresetId: {preset_id} Name: \"{seat}\" DefaultPosition: False TakeSnapshot: False\r\n"
    )
    print(f"[Serial →] {cmd.strip()}")
    codec_serial.write(cmd.encode()); codec_serial.flush()
    return jsonify({'status': 'ok', 'presetId': preset_id, 'cameraId': camera_id}), 200

@app.route('/api/cam/set', methods=['POST'])
def set_cam_state():
    data = request.json or {}
    cam_id = str(data.get('cam', current_cam))
    pan = int(data.get('pan', 0)); tilt = int(data.get('tilt', 0)); zoom = int(data.get('zoom', 100))
    cam_state[cam_id] = {'pan': pan, 'tilt': tilt, 'zoom': zoom}
    return jsonify({'status': 'updated', 'cam': cam_id}), 200

# -------- Mike 页面及接口 --------
@app.route('/mike')
def mike_page():
    return render_template('Mike.html')

@app.route('/api/discussion/seats/<int:seat>', methods=['PUT'])
def set_microphone_state(seat):
    data = request.json or {}
    if 'microphoneOn' not in data or 'requestingToSpeak' not in data:
        return jsonify({'error': '缺少 microphoneOn 或 requestingToSpeak'}), 400
    url = f"{DCERNO_BASE}/api/discussion/seats/{seat}"
    try:
        resp = requests.put(url, headers=MIC_HEADERS, json=data, verify=False)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'连接 D-Cerno 失败: {e}'}), 500

@app.route('/api/discussion/speakers', methods=['GET'])
def get_speaking_seats():
    url = f"{DCERNO_BASE}/api/discussion/speakers"
    try:
        resp = requests.get(url, headers=MIC_HEADERS, verify=False)
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'连接失败: {e}'}), 500

# @app.route('/api/discussion/speakers', methods=['GET'])
# def get_speaking_seats():
#     return jsonify([choice([6,1])])



# -------- 自动追踪线程 --------
def tracking_loop():
    print("[Tracking] tracking_loop 已启动")
    last_preset_id = None

    while True:
        if not is_tracking_enabled:
            time.sleep(1)
            continue
        print("[Tracking] tracking 已开启，正在监听发言人...")   
        try:
            #print(f"[Tracking] speakers = {speakers} ({type(speakers)})")
            resp = requests.get(f"{DCERNO_BASE}/api/discussion/speakers", headers=MIC_HEADERS, verify=False)
            if resp.ok:
                speakers = resp.json()
                print(f"[Tracking] speakers = {speakers} ({type(speakers)})")
                if isinstance(speakers, list):
                    if not speakers:
                        # 没人发言 → 调用 Camera 2 的全景预设 32
                        if last_preset_id != 32 and serial_enabled:

                            #cmd = "xCommand Camera Preset Activate CameraId: 2 PresetId: 32\r\n"
                            cmd = ("xCommand Video Input setMainVideoSource SourceId: 1\r\n"
                                   "xCommand Camera Preset Activate PresetId: 14\r\n")
                            codec_serial.write(cmd.encode()); codec_serial.flush()
                            print("[Tracking] 无人发言 → 调用 Camera 2 的全景 PresetId 32")
                            last_preset_id = 32
                        time.sleep(1)
                        continue

                    latest = speakers[-1]  # 最新发言人编号
                    if latest >=6:
                        camera_id = 1
                        preset_id = latest + 20
                    else:
                        camera_id = 2
                        preset_id = latest + 20

                    if preset_id != last_preset_id and serial_enabled:
                       
                        cmd = (f"xCommand Video Input setMainVideoSource SourceId: {camera_id}\r\n"
                               f"xCommand Camera Preset Activate PresetId: {preset_id}\r\n")
                        # cmd = {f"xCommand Camera Preset Activate PresetId: {preset_id}\r\n"}
                        codec_serial.write(cmd.encode()); codec_serial.flush()
                        print(f"[Tracking] 最新发言 seat{latest} → Camera {camera_id} Preset {preset_id}")
                        last_preset_id = preset_id

        except Exception as e:
            print(f"[Tracking error] {e}")

        time.sleep(1)


# # 模拟测试
# def tracking_loop():
#     print("[Tracking] tracking_loop 已启动")
#     last_preset_id = None

#     while True:
#         if not is_tracking_enabled:
#             time.sleep(1)
#             continue

#         print("[Tracking] tracking 已开启，正在监听发言人...")
#         try:
#             # —— 调试用：临时指向本地模拟接口 —— 
#             resp = requests.get("http://127.0.0.1:5000/api/discussion/speakers")
#             # —— 正式用：调用设备接口 —— 
#             # resp = requests.get(f"{DCERNO_BASE}/api/discussion/speakers", headers=MIC_HEADERS, verify=False)

#             if not resp.ok:
#                 print(f"[Tracking] 接口返回 {resp.status_code}: {resp.text}")
#                 time.sleep(1)
#                 continue

#             speakers = resp.json()
#             print(f"[Tracking] speakers = {speakers} ({type(speakers)})")

#             if not isinstance(speakers, list):
#                 print("[Tracking] 返回格式不是列表，跳过")
#                 time.sleep(1)
#                 continue

#             # 没人发言
#             if not speakers:
#                 if last_preset_id != 32 and serial_enabled:
#                     cmd = (
#                         "xConfiguration Video Input MainVideoSource: 2\r\n"
#                         "xCommand Camera Preset Activate CameraId: 2 PresetId: 32\r\n")
#                     codec_serial.write(cmd.encode()); codec_serial.flush()

#                     print("[Tracking] 无人发言 → Camera 2 Preset 32（全景模式）")
#                     last_preset_id = 32
#                 time.sleep(1)
#                 continue

#             # 最新发言人
#             latest = speakers[-1]
#             if latest == 6:
#                 camera_id, preset_id = 1, 26
#             else:
#                 camera_id, preset_id = 2, latest + 20

#             if preset_id != last_preset_id and serial_enabled:
#                 cmd = (
#                     f"xConfiguration Video Input MainVideoSource: {camera_id}\r\n"
#                     f"xCommand Camera Preset Activate CameraId: {camera_id} PresetId: {preset_id}\r\n"
#                     )
#                 codec_serial.write(cmd.encode()); codec_serial.flush()
#                 print(f"[Tracking] 当前显示的摄像头 -> Camera {camera_id}")
#                 print(f"[Tracking] 最新发言 seat{latest} → Camera {camera_id} Preset {preset_id}")
#                 last_preset_id = preset_id

#         except Exception as e:
#             print(f"[Tracking error] {e}")

#         time.sleep(1)


# def open_browser():
#     #webbrowser.open("http://localhost:5000/camera")
#     webbrowser.open_new_tab("http://localhost:5000/mike")

# if __name__ == '__main__':
#     t = threading.Thread(target=tracking_loop, daemon=True)
#     t.start()
#     threading.Timer(1.0, open_browser).start()
#     app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)

def start_flask():
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    # 启动 tracking 线程
    t = threading.Thread(target=tracking_loop, daemon=True)
    t.start()

    # 启动 Flask 服务线程
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # 延迟1秒后打开窗口（保证 Flask 已启动）
    time.sleep(1)

    # 弹出窗口显示 mike 页面（也可以改成 camera）
    webview.create_window("Conference Control System", "http://localhost:5000/mike")
    webview.start()