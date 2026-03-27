"""
DDM Phase 1 - Backend API Server
FastAPI-based backend for Core IE Service, Simulation Service, and Auth Service
"""

import csv
import hashlib
import json
import logging
from pathlib import Path
import re
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from typing import Any, Dict, List, Optional, Set, Tuple

import jwt
from copy import deepcopy
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ============================================
# App Configuration
# ============================================

app = FastAPI(
    title="DDM Phase 1 API",
    description="Ding Ding Mao - Industrial Engineering Platform",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()
SECRET_KEY = "ddm-secret-key-phase1"
ALGORITHM = "HS256"

# ============================================
# Enums & Constants
# ============================================

class UserRole(str, Enum):
    ENGINEER = "Engineer"
    MANAGER = "Manager"
    OPERATOR = "Operator"

class SkillLevel(str, Enum):
    NOVICE = "Novice"
    PROFICIENT = "Proficient"
    EXPERT = "Expert"

class SOPStatus(str, Enum):
    DRAFT = "Draft"
    REVIEWED = "Reviewed"
    PUBLISHED = "Published"

class AuditAction(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    APPROVE = "APPROVE"
    PUBLISH = "PUBLISH"

logger = logging.getLogger(__name__)

TMU_FACTOR = 0.036

SKILL_EFFICIENCY_MAP = {
    SkillLevel.NOVICE: 0.8,
    SkillLevel.PROFICIENT: 1.0,
    SkillLevel.EXPERT: 1.2
}

# ============================================
# MiniMOST TMU Index Tables (per CSV spec)
# ============================================

# A Index - Distance to TMU (hand reach/move)
A_INDEX_TABLE = [
    {"max_cm": 2.5, "tmu": 0},
    {"max_cm": 5, "tmu": 1},
    {"max_cm": 10, "tmu": 3},
    {"max_cm": 20, "tmu": 6},
    {"max_cm": 35, "tmu": 10},
    {"max_cm": 60, "tmu": 16},
    {"max_cm": 120, "tmu": 24},  # >60cm reach / small walk
    {"max_cm": 9999, "tmu": 32}  # multi-step walk
]

# B Index - Body Motion (angle degrees for hand rotation)
B_INDEX_TABLE = [
    {"max_angle": 30, "tmu": 0},
    {"max_angle": 60, "tmu": 1},
    {"max_angle": 120, "tmu": 3},
    {"max_angle": 180, "tmu": 6}
]

# B Index - Foot/Step Motion (cm distance)
B_FOOT_INDEX_TABLE = [
    {"max_cm": 0, "tmu": 0},
    {"max_cm": 20, "tmu": 6},   # Foot action
    {"max_cm": 30, "tmu": 10},
    {"max_cm": 45, "tmu": 16},  # 1 step
    {"max_cm": 65, "tmu": 24},
    {"max_cm": 200, "tmu": 32}  # 2 steps+
]

# M Index - Controlled Move (per MOST standard spec)
# Single select: Take maximum value from Distance + Hand Angle + Foot Distance + Rotation
# Example: Move distance <=10cm (6 TMU) + hand angle <=180° (10 TMU) + no foot movement → Max = 10 TMU
M_INDEX_TABLE = [
    # Distance lookup (cm)
    {"max_cm": 2.5, "tmu": 3, "type": "distance", "description": "Button press"},
    {"max_cm": 10, "tmu": 6, "type": "distance"},
    {"max_cm": 25, "tmu": 10, "type": "distance"},
    {"max_cm": 45, "tmu": 16, "type": "distance", "description": "Seat or Unseat"},
    {"max_cm": 75, "tmu": 24, "type": "distance"},
    {"max_cm": 9999, "tmu": 32, "type": "distance"},
]

# M Index - Hand angle (degrees)
M_HAND_ANGLE_TABLE = [
    {"max_angle": 90, "tmu": 6},
    {"max_angle": 180, "tmu": 10},
]

# M Index - Foot distance (cm)
M_FOOT_DISTANCE_TABLE = [
    {"max_cm": 25, "tmu": 10},
    {"max_cm": 40, "tmu": 16},
    {"max_cm": 55, "tmu": 24},
    {"max_cm": 75, "tmu": 32},
    {"max_cm": 9999, "tmu": 42},  # >75cm
]

# M Index - Rotation (number of turns, diameter)
M_ROTATION_TABLE = {
    # Diameter <= 12.5cm
    "small": {
        1: 16,  # 1 turn
        2: 32,  # 2 turns
        3: 42,  # 3 turns
    },
    # Diameter <= 50cm
    "large": {
        1: 24,  # 1圈
        2: 42,  # 2圈
    }
}

# P Index - 加算修飾符 (根據 MOST邏輯詳解版)
P_MODIFIERS_TABLE = {
    "对准": {"tmu_addon": 8, "show_in_wi": True, "description": "精度<4mm"},
    "插入": {"tmu_addon": 8, "show_in_wi": True},
    "较难处理": {"tmu_addon": 8, "show_in_wi": False},
    "卡合": {"tmu_addon": 16, "show_in_wi": True},
    "施加压力": {"tmu_addon": 16, "show_in_wi": False},
}

# Tool Actions 距離查表 (根據 MOST邏輯詳解版)
# 適用於: 理/穿/推/拉/貼附/去除/撕除/撕开/折/擦拭
TOOL_DISTANCE_TABLE = [
    {"max_cm": 2.5, "tmu": 3},   # <=1 in (2.5 cm)
    {"max_cm": 10, "tmu": 6},    # <=4 in (10 cm)
    {"max_cm": 25, "tmu": 10},   # <=10 in (25 cm)
    {"max_cm": 45, "tmu": 16},   # <=18 in (45 cm)
    {"max_cm": 75, "tmu": 24},   # <=30 in (75 cm)
]

# Fixed TMU Values (per CSV spec)
FIXED_TMU_VALUES = {
    "锁附固定": 6,      # 0.216 seconds
    "按动按钮": 3,      # Fixed 3 TMU
    "滑出螺丝": 3,      # Fixed 3 TMU
    "打印": 0,          # Machine time, excluded
}

# Sequence Model Formulas
SEQUENCE_MODELS = {
    "GENERAL": "A + B + G + A + B + P + A",      # 一般移动: 從+從哪裡+G+目標物+到哪裡+P+A
    "CONTROLLED": "A + B + G + M + X + I + A"    # 控制移动: 從+從哪裡+G+目標物+M+X+I+到哪裡
}

# Action Verb Categories (per CSV 員工技能匹配表)
# TMU 預設值根據 MOST邏輯詳解版 規格定義
ACTION_SKILL_MAPPING = {
    # G - Grasp actions (抓取動作)
    # 輕按/接觸/輕拍: TMU=3 (接觸類)
    # 抓握/抓取/重新抓握: TMU=6 
    # 換手/拿取(選取): TMU=10
    # 拿取(選取小)/拔出: TMU=16
    # 拿取(收集): TMU=24
    "轻按": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 3},
    "接触": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 3},
    "轻拍": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 3},
    "抓握": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 6},
    "抓取": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 6},
    "重新抓握": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 6},
    "换手": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 10},
    "拿取": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 10},  # 選取
    "拿取_选取小": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 16},  # 選取(小)
    "拿取_收集": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 24},  # 收集
    "拔出": {"code": "G", "skill": "一般作业", "skill_no": None, "tmu_default": 16},
    
    # M - Move/Controlled actions
    # 按动按钮/滑出螺丝: 固定值
    "按动按钮": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "fixed": True},
    "滑出螺丝": {"code": "M", "skill": "锁螺丝", "skill_no": "FATP01", "tmu_default": 3, "fixed": True},
    # Tool Actions - 支援距離查表 (distance_lookup: True)
    # TMU 規則: <=2.5cm=3, <=10cm=6, <=25cm=10, <=45cm=16, <=75cm=24
    "理": {"code": "M", "skill": "理線", "skill_no": "FATP03", "tmu_default": 3, "distance_lookup": True, "ctq": True},
    "穿": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "推": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "拉": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "贴附": {"code": "M", "skill": "貼附", "skill_no": "FATP12", "tmu_default": 3, "distance_lookup": True},
    "去除": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "撕除": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "撕开": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 3, "distance_lookup": True},
    "折": {"code": "M", "skill": "理線", "skill_no": "FATP03", "tmu_default": 3, "distance_lookup": True, "ctq": True},
    "擦拭": {"code": "M", "skill": "擦拭", "skill_no": "FATP13", "tmu_default": 3, "distance_lookup": True},
    # 手度: 支援角度查表 (angle_lookup: True), <=90° TMU=6, <=180° TMU=10
    "手度": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 6, "angle_lookup": True},
    # 腳步: 支援距離查表 (foot_lookup: True), >75cm TMU=42
    "脚步": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 6, "foot_lookup": True},
    # 旋轉: 小直徑(≤12.5cm) 1圈=16, 2圈=32, 3圈=42; 大直徑(≤50cm) 1圈=24, 2圈=42
    "旋转": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 16},  # 小直徑 (≤12.5cm)
    "旋转_大直径": {"code": "M", "skill": "一般作业", "skill_no": None, "tmu_default": 24},  # 大直徑 (≤50cm)
    
    # P - Place actions (放置動作)
    # 丟/保持住: TMU=3
    # 放(無方向): TMU=6, 放(多種方向): TMU=10, 放(一種方向): TMU=16
    # 組(多種方向): TMU=10, 組(一種方向): TMU=16
    # 加算修飾符: 對準(精度<4mm)+8, 插入+8, 較難處理+8, 卡合+16, 施加壓力+16
    "丢": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 3},
    "保持住": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 3},
    "放": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 6},  # 無方向
    "放_多种方向": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 10},  # 多種方向
    "放_一种方向": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 16},  # 一種方向
    "组": {"code": "P", "skill": "裝配", "skill_no": "FATP07", "tmu_default": 10, "ctq": True},  # 多種方向
    "组_一种方向": {"code": "P", "skill": "裝配", "skill_no": "FATP07", "tmu_default": 16, "ctq": True},  # 一種方向
    "复选": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 0},
    # P 加算修飾符 (tmu_addon 表示加算值，不是預設值)
    "对准": {"code": "P", "skill": "插金手指", "skill_no": "FATP05", "tmu_default": 0, "tmu_addon": 8, "is_modifier": True, "ctq": True},
    "插入": {"code": "P", "skill": "插線", "skill_no": "FATP04", "tmu_default": 0, "tmu_addon": 8, "is_modifier": True, "ctq": True},
    "较难处理": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 0, "tmu_addon": 8, "is_modifier": True, "hidden_in_wi": True},
    "卡合": {"code": "P", "skill": "卡合", "skill_no": "FATP06", "tmu_default": 0, "tmu_addon": 16, "is_modifier": True, "ctq": True},
    "施加壓力": {"code": "P", "skill": "一般作业", "skill_no": None, "tmu_default": 0, "tmu_addon": 16, "is_modifier": True, "hidden_in_wi": True},
    
    # X - Process actions
    # 動態時間計算: dynamic_time: True 表示 TMU = 時間(S) / 0.036
    # 固定時間: fixed: True 表示 TMU = tmu_default
    "压合": {"code": "X", "skill": "壓合&熱熔", "skill_no": "FATP08", "tmu_default": 6, "dynamic_time": True, "ctq": True},
    "卡合压合": {"code": "X", "skill": "壓合&熱熔", "skill_no": "FATP08", "tmu_default": 6, "dynamic_time": True, "ctq": True},
    "热熔": {"code": "X", "skill": "壓合&熱熔", "skill_no": "FATP08", "tmu_default": 6, "dynamic_time": True, "ctq": True},
    "点胶": {"code": "X", "skill": "點膠&Bonding", "skill_no": "FATP10", "tmu_default": 6, "dynamic_time": True},
    "镭雕": {"code": "X", "skill": "半自動/自動化設備操作", "skill_no": "FATP11", "tmu_default": 6, "dynamic_time": True},
    # 固定時間動作: 0.216S = 6 TMU
    "锁附固定": {"code": "X", "skill": "鎖螺絲", "skill_no": "FATP01", "tmu_default": 6, "fixed": True, "ctq": True},
    # 刷碼類動作：0.216S = 6 TMU (固定值)
    "扫描": {"code": "X", "skill": "刷槍", "skill_no": "FATP02", "tmu_default": 6, "fixed": True},
    "刷PPID": {"code": "X", "skill": "刷槍", "skill_no": "FATP02", "tmu_default": 6, "fixed": True},
    "刷工单二维码": {"code": "X", "skill": "刷槍", "skill_no": "FATP02", "tmu_default": 6, "fixed": True},
    "刷条形码": {"code": "X", "skill": "刷槍", "skill_no": "FATP02", "tmu_default": 6, "fixed": True},
    "打印": {"code": "X", "skill": "半自動/自動化設備操作", "skill_no": "FATP11", "tmu_default": 0},
    
    # I - Align/Inspect actions (對齊/檢查動作)
    # 并检查/并确认 (正常視線): TMU=6
    # 并对准 (正常視線 到点): TMU=10
    # 并对齐 (正常視線 到兩點): TMU=16
    # 并检查/并确认 (視線外): TMU=16
    # 并对准 (視線外 到点): TMU=24
    # 并对齐 (視線外 到兩點): TMU=32
    "并检查": {"code": "I", "skill": "目視", "skill_no": "FATP26", "tmu_default": 6, "tmu_out_of_sight": 16},
    "并确认": {"code": "I", "skill": "目視", "skill_no": "FATP26", "tmu_default": 6, "tmu_out_of_sight": 16},
    "并对准": {"code": "I", "skill": "目視", "skill_no": "FATP26", "tmu_default": 10, "tmu_out_of_sight": 24},
    "并对齐": {"code": "I", "skill": "目視", "skill_no": "FATP26", "tmu_default": 16, "tmu_out_of_sight": 32},
}

# Hand Selection Options
HAND_OPTIONS = ["左手", "右手", "双手"]

# Level System Function Characters
LEVEL_SYSTEM_CHARS = {
    "main": "主要順序分層 - 所有層級文件的最初分層必須使用main",
    "sub": "次要順序分層 - 層級深度到2以後所使用的順序編制",
    "cub": "次要固化分層 - 將動作固化成系統無法分割的動作區塊",
    "nb": "分割限制元素 - 可以限制擁有相同元素標籤的層級數量"
}

# Object / Location Master Data (per CSV catalog)
OBJECT_LIBRARY = [
    # ===== 背板/轉接卡類 =====
    {"id": "obj-4nvme-bp", "category": "背板", "sub_category": "NVMe", "name": "4NVME BP", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-bf3-cage", "category": "擴充卡", "sub_category": "BF3", "name": "BF3 cage", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-bf3-riser", "category": "擴充卡", "sub_category": "BF3", "name": "BF3 riser", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-bf3-nic", "category": "網卡", "sub_category": "BF3", "name": "BF3網卡", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-bp-sideband", "category": "線材", "sub_category": "BP", "name": "BP sideband線", "glove_type": "左手半指+右手指套", "ctq": False},
    {"id": "obj-bp-power", "category": "線材", "sub_category": "BP", "name": "BP供電線", "glove_type": "左手半指+右手指套", "ctq": False},
    # ===== CPU/DPU類 =====
    {"id": "obj-cpu", "category": "處理器", "sub_category": "CPU", "name": "CPU", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-cpu-bracket", "category": "支架", "sub_category": "CPU", "name": "CPU支架", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-cpu-chip", "category": "處理器", "sub_category": "MPU", "name": "CPU晶片(MPU)", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-dpu", "category": "處理器", "sub_category": "DPU", "name": "DPU", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-dpu2-cable", "category": "線材", "sub_category": "DPU", "name": "DPU2轉接線", "glove_type": "左手半指+右手指套", "ctq": False},
    {"id": "obj-dpu-bracket", "category": "支架", "sub_category": "DPU", "name": "DPU支架", "glove_type": "兩只半指手套", "ctq": False},
    # ===== GPU類 =====
    {"id": "obj-gpu-cage", "category": "擴充卡", "sub_category": "GPU", "name": "GPU Cage", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-gpu-riser", "category": "擴充卡", "sub_category": "GPU", "name": "GPU Riser", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-gpu-cable", "category": "線材", "sub_category": "GPU", "name": "GPU轉接線", "glove_type": "左手半指+右手指套", "ctq": False},
    # ===== 標籤類 =====
    {"id": "obj-hood-label", "category": "標籤", "sub_category": "Label", "name": "HOOD LABEL", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-rfid-label", "category": "標籤", "sub_category": "RFID", "name": "RFID空白標籤", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-net-label", "category": "標籤", "sub_category": "Label", "name": "網口標籤", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-net-order-label", "category": "標籤", "sub_category": "Label", "name": "網口順序標籤", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-sticker", "category": "標籤", "sub_category": "Label", "name": "標貼", "glove_type": "一般作業手套", "ctq": False},
    # ===== I/O與網卡類 =====
    {"id": "obj-io-card", "category": "介面卡", "sub_category": "IO", "name": "I/O卡", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-lan-switch", "category": "網卡", "sub_category": "Switch", "name": "LAN switch", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-nic", "category": "網卡", "sub_category": "NIC", "name": "NIC卡", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-ocp-dummy", "category": "網卡", "sub_category": "OCP", "name": "OCP DUMMY", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-ocp-card", "category": "網卡", "sub_category": "OCP", "name": "OCP卡", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-ocp-cable", "category": "線材", "sub_category": "OCP", "name": "OCP轉接線", "glove_type": "左手半指+右手指套", "ctq": False},
    {"id": "obj-ocp-label", "category": "標籤", "sub_category": "OCP", "name": "OCP標籤", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-server-nic", "category": "網卡", "sub_category": "NIC", "name": "網卡(服務器用)", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-net-cable", "category": "線材", "sub_category": "Cable", "name": "網線(有接頭)", "glove_type": "左手半指+右手指套", "ctq": False},
    # ===== 主板/背板類 =====
    {"id": "obj-mlb", "category": "主板/MLB", "sub_category": "MLB", "name": "MLB", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-server-mb", "category": "主板/MLB", "sub_category": "MLB", "name": "服務器主機板", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-backplane", "category": "背板", "sub_category": "BP", "name": "背板", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-adapter-board", "category": "轉接板", "sub_category": "Adapter", "name": "轉接板(服務器零件)", "glove_type": "兩只半指手套", "ctq": True},
    # ===== 線材類 =====
    {"id": "obj-rear-io-cable", "category": "線材", "sub_category": "IO", "name": "Rear IO Cable", "glove_type": "左手半指+右手指套", "ctq": False},
    {"id": "obj-top-cable", "category": "線材", "sub_category": "Cable", "name": "TOP線", "glove_type": "左手半指+右手指套", "ctq": False},
    {"id": "obj-wire", "category": "線材", "sub_category": "Wire", "name": "電線", "glove_type": "左手半指+右手指套", "ctq": False},
    # ===== 儲存裝置類 =====
    {"id": "obj-ssd", "category": "儲存裝置", "sub_category": "SSD", "name": "SSD固態硬盤", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-dimm", "category": "記憶體", "sub_category": "DIMM", "name": "DIMM內存", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-hdd", "category": "儲存裝置", "sub_category": "HDD", "name": "HDD", "glove_type": "兩只無塵手套", "ctq": True},
    {"id": "obj-fake-dimm", "category": "假件", "sub_category": "DIMM", "name": "假DIMM", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-fake-ssd", "category": "假件", "sub_category": "SSD", "name": "假SSD", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-hdd-bracket", "category": "支架", "sub_category": "HDD", "name": "硬盤支架", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-hdd-shell", "category": "機殼", "sub_category": "HDD", "name": "硬盤模殼", "glove_type": "兩只半指手套", "ctq": False},
    # ===== 散熱類 =====
    {"id": "obj-cold-plate", "category": "散熱", "sub_category": "ColdPlate", "name": "冷板", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-liquid-bracket", "category": "支架", "sub_category": "Liquid", "name": "液冷管支架", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-heatsink", "category": "散熱", "sub_category": "Heatsink", "name": "散熱片", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-cooling-fan", "category": "散熱", "sub_category": "Fan", "name": "散熱風扇", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-thermal-module", "category": "散熱", "sub_category": "Module", "name": "散熱模組", "glove_type": "兩只半指手套", "ctq": True},
    # ===== 風扇類 =====
    {"id": "obj-fan", "category": "風扇", "sub_category": "Fan", "name": "風扇", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-fan-shell", "category": "風扇", "sub_category": "Shell", "name": "風扇外殼", "glove_type": "一般作業手套", "ctq": False},
    # ===== 擋板/擋罩類 =====
    {"id": "obj-small-baffle", "category": "擋板", "sub_category": "Baffle", "name": "小擋風罩", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-long-plate", "category": "擋板", "sub_category": "Plate", "name": "長擋片", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-air-duct", "category": "導風罩", "sub_category": "Duct", "name": "導風罩", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-mesh-plate", "category": "擋板", "sub_category": "Mesh", "name": "網狀擋片", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-mesh-baffle", "category": "擋板", "sub_category": "Mesh", "name": "網狀擋板", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-rear-baffle", "category": "擋板", "sub_category": "Baffle", "name": "尾部擋板", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-plate", "category": "擋板", "sub_category": "Plate", "name": "擋片", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-wind-baffle", "category": "導風罩", "sub_category": "Baffle", "name": "擋風罩", "glove_type": "一般作業手套", "ctq": False},
    # ===== 機殼/結構類 =====
    {"id": "obj-main-chassis", "category": "機殼", "sub_category": "Chassis", "name": "主機殼", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-chassis", "category": "機殼", "sub_category": "Chassis", "name": "機殼", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-beam", "category": "結構件", "sub_category": "Beam", "name": "橫樑", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-right-ear", "category": "結構件", "sub_category": "Ear", "name": "右耳", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-left-ear", "category": "結構件", "sub_category": "Ear", "name": "左耳", "glove_type": "一般作業手套", "ctq": False},
    # ===== 支架類 =====
    {"id": "obj-bracket", "category": "支架", "sub_category": "Bracket", "name": "支架", "glove_type": "兩只半指手套", "ctq": False},
    {"id": "obj-cable-bracket", "category": "支架", "sub_category": "Cable", "name": "轉接線支架", "glove_type": "兩只半指手套", "ctq": False},
    # ===== Riser類 =====
    {"id": "obj-front-riser", "category": "擴充卡", "sub_category": "Riser", "name": "前置riser", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-front-riser-cage", "category": "擴充卡", "sub_category": "Riser", "name": "前置riser cage", "glove_type": "兩只半指手套", "ctq": False},
    # ===== 電源類 =====
    {"id": "obj-power", "category": "電源", "sub_category": "PSU", "name": "電源", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-psu", "category": "電源", "sub_category": "PSU", "name": "電源供應器", "glove_type": "兩只半指手套", "ctq": True},
    # ===== 標準件/耗材類 =====
    {"id": "obj-cable-tie", "category": "標準件", "sub_category": "Tie", "name": "紮帶", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-plastic-fastener", "category": "標準件", "sub_category": "Fastener", "name": "塑膠緊固件", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-plastic-bag", "category": "包材", "sub_category": "Bag", "name": "塑膠袋", "glove_type": "一般作業手套", "ctq": False},
    {"id": "obj-screw", "category": "標準件", "sub_category": "Screw", "name": "螺絲", "glove_type": "兩只半指手套", "ctq": True},
    {"id": "obj-nut", "category": "標準件", "sub_category": "Nut", "name": "螺母", "glove_type": "兩只半指手套", "ctq": False},
]

COMPONENT_LIBRARY = [
    {"id": "comp-001", "name_cn": "MLB(主機板)", "name_en": "Motherboard", "category": "Main Part"},
    {"id": "comp-002", "name_cn": "內存(Memory)", "name_en": "Memory Module", "category": "Memory"},
    {"id": "comp-003", "name_cn": "假DIMM", "name_en": "Dummy DIMM", "category": "Dummy Part"},
    {"id": "comp-004", "name_cn": "DIMM", "name_en": "DIMM Module", "category": "Memory"},
    {"id": "comp-005", "name_cn": "拇指螺絲", "name_en": "Thumb Screw", "category": "Standard Part"},
    {"id": "comp-006", "name_cn": "主板", "name_en": "Main Board", "category": "Main Part"},
    {"id": "comp-007", "name_cn": "機箱", "name_en": "Chassis", "category": "Main Part"},
    {"id": "comp-008", "name_cn": "CPU散熱器", "name_en": "CPU Heatsink", "category": "Standard Part"},
]

LOCATION_LIBRARY = [
    {"id": "loc-001", "name": "潔淨柵工作台"},
    {"id": "loc-002", "name": "DIMM槽"},
    {"id": "loc-003", "name": "機箱內"},
    {"id": "loc-004", "name": "DIMM壓合治具"},
    {"id": "loc-005", "name": "主板DIMM槽"},
    {"id": "loc-006", "name": "MVS站"},
    {"id": "loc-007", "name": "離子風扇下"},
]

FROM_LOCATIONS = [
    {"id": "from-rack", "name": "捡料架"},
    {"id": "from-cart", "name": "周轉車"},
    {"id": "from-bin", "name": "百寶箱"},
    {"id": "from-buffer", "name": "站點Buffer"}
]

TO_LOCATIONS = [
    {"id": "to-mb", "name": "主板"},
    {"id": "to-lcd", "name": "LCD"},
    {"id": "to-chassis", "name": "機箱"},
    {"id": "to-trash", "name": "垃圾桶"},
    {"id": "to-fixture", "name": "治具"}
]

REFERENCE_POINTS = [
    {"id": "ref-mes", "name": "MES工站顯示"},
    {"id": "ref-light", "name": "工位指示燈"},
    {"id": "ref-label", "name": "條碼標籤區"},
    {"id": "ref-fixture", "name": "治具定位點"}
]

PRECAUTIONS = [
    {
        "id": "prec-001",
        "process": "BASY",
        "category": "ESD",
        "index_code": "A1",
        "description": "操作MLB/LCD需配戴兩只半指手套，避免ESD與指紋污染"
    },
    {
        "id": "prec-002",
        "process": "BASY",
        "category": "治具",
        "index_code": "B1",
        "description": "進入治具前需確認定位銷及防呆結構，避免撞傷物料"
    },
    {
        "id": "prec-003",
        "process": "BPKG",
        "category": "品檢",
        "index_code": "I",
        "description": "封箱前需雙人複檢條碼與出貨單對應"
    }
]

GLOVE_RULES = [
    {"id": "glove-001", "object_category": "高單價物料", "action": "放", "glove_type": "兩只半指手套"},
    {"id": "glove-002", "object_category": "主板/MLB", "action": "組", "glove_type": "兩只半指手套"},
    {"id": "glove-003", "object_category": "線材", "action": "理", "glove_type": "左手半指+右手指套"},
    {"id": "glove-004", "object_category": "包材", "action": "裝箱", "glove_type": "一般作業手套"}
]

LEVEL_SYSTEM_TEMPLATES = [
    {
        "id": "lvl-001",
        "name": "LCD安裝-主流程",
        "sequence": ["main:1", "main:2", "sub1:2.1", "sub1:2.2", "cub1:2.2.1"],
        "notes": "LCD屬CTQ，sub層需要nb限制不超過2個元素"
    },
    {
        "id": "lvl-002",
        "name": "線材整理-雙手動作",
        "sequence": ["main:1", "sub1:1.1", "sub1:1.2", "nb:hand=2"],
        "notes": "線材整理需限制為雙手同步，以nb元素約束手數"
    }
]

ION_FAN_BINDINGS = [
    {
        "id": "ionfan-default",
        "object_category": "高單價物料",
        "object_name": "LCD",
        "note": "操作此物料時必須開啟離子風扇"
    }
]

MI_NAMING_RULES = [
    {
        "id": "mi-field-001",
        "field_label": "機種五碼",
        "slug": "model_code",
        "position": 1,
        "description": "手動輸入機種代碼，未輸入時預設為All model",
        "options": [],
        "example": "HDL50",
        "required": True
    },
    {
        "id": "mi-field-002",
        "field_label": "製程別",
        "slug": "process",
        "position": 4,
        "description": "ASSY / PACK / DOCK / SMT 等流程",
        "options": ["ASSY", "PACK", "SUB", "SMT"],
        "example": "ASSY",
        "required": True
    }
]

LEVEL_GUIDELINES = [
    {
        "id": "lvl-guide-default",
        "title": "Level System 基礎",
        "body": "main 為主要層級，sub/cub 用於細分，nb 作為分割限制元素。"
    }
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DDM_STRUCTURE_PATH = DATA_DIR / "ddm_structure.csv"

# ============================================
# JSON Persistence Configuration
# ============================================
DB_JSON_PATH = DATA_DIR / "db_persistent.json"
AUTO_SAVE_ENABLED = True  # Set to False to disable auto-save

# Keys to persist (exclude users for security, exclude computed/cached data)
PERSISTENT_KEYS = [
    "syntax_library",
    "component_library", 
    "tool_library",
    "location_library",
    "object_library",
    "from_locations",
    "to_locations",
    "reference_points",
    "precautions",
    "glove_rules",
    "level_system_templates",
    "ion_fan_bindings",
    "mi_naming_rules",
    "level_guidelines",
    "employees",
    "projects",
    "sop_versions",
    "actions",
    "stations",
    "audit_logs",
    "simulation_results",  # 產線平衡模擬結果歷史
    "level_entries"  # Level System entries per project
]


def _safe_strip(value: Optional[str]) -> str:
    return value.strip() if value else ""


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(part for part in parts if part)
    if not raw:
        raw = uuid.uuid4().hex
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _split_multi(value: str) -> List[str]:
    if not value:
        return []
    tokens = re.split(r"[、/,;；，\\s]+", value)
    return [token for token in (t.strip() for t in tokens) if token]


def _slugify_label(value: str, fallback: str = "field") -> str:
    if not value:
        return fallback
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or fallback


def load_ddm_structure_catalogs() -> Dict[str, List[Dict[str, Any]]]:
    catalogs = {
        "objects": [],
        "components": [],
        "from_locations": [],
        "to_locations": [],
        "reference_points": [],
        "glove_rules": [],
        "precautions": [],
        "location_library": [],
        "ion_fan_bindings": [],
        "mi_naming_rules": [],
        "level_guidelines": [],
    }
    if not DDM_STRUCTURE_PATH.exists():
        return catalogs

    try:
        with DDM_STRUCTURE_PATH.open(encoding="utf-8-sig", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            object_headers: Dict[int, str] = {}
            object_seen: Set[Tuple[str, str, str]] = set()
            glove_headers: Dict[int, str] = {}
            precaution_header_seen = False
            from_seen: Set[str] = set()
            to_seen: Set[str] = set()
            ref_seen: Set[str] = set()
            component_seen: Set[str] = set()
            ionfan_seen: Set[Tuple[str, str, str]] = set()
            mi_headers: Dict[int, str] = {}
            mi_meta: Dict[int, Dict[str, Any]] = {}
            general_mi_notes: List[str] = []
            level_guideline_seen: Set[Tuple[str, str]] = set()

            for row in reader:
                name = _safe_strip(row.get("Name"))
                columns = [_safe_strip(row.get(f"Column{i}")) for i in range(1, 12)]

                if name == "目標物":
                    header_hits = sum(1 for col in columns if col in {"四大件", "高单价", "排线", "板", "铁件", "Rubber", "其它", "包材/配件", "包材&配件", "ASSY", "PACK"})
                    if header_hits >= 5:
                        object_headers = {idx: value for idx, value in enumerate(columns, start=1) if value}
                        continue
                    if not any(columns):
                        continue
                    family = columns[0] or object_headers.get(1, "未分類")
                    for idx in range(2, 12):
                        value = columns[idx - 1]
                        if not value:
                            continue
                        category = object_headers.get(idx, f"Column{idx}")
                        key = (family, category, value)
                        if key in object_seen:
                            continue
                        entry = {
                            "id": _stable_id("obj", *key),
                            "family": family,
                            "category": category,
                            "name": value,
                            "ctq": category in {"高单价", "四大件"},
                        }
                        catalogs["objects"].append(entry)
                        object_seen.add(key)
                    continue

                if name == "手套使用的定義":
                    if not glove_headers:
                        glove_headers = {idx: value for idx, value in enumerate(columns, start=1) if value}
                        continue
                    category = columns[0] or "未分類"
                    object_name = columns[1] or columns[2]
                    glove_type = columns[7] or columns[8]
                    for idx, action_column in enumerate((4, 5, 6, 7), start=4):
                        actions = _split_multi(columns[action_column - 1])
                        code_hint = glove_headers.get(idx)
                        for action in actions:
                            catalogs["glove_rules"].append({
                                "id": _stable_id("glove", category, object_name or code_hint or action, action),
                                "object_category": category,
                                "object_name": object_name,
                                "action": action,
                                "glove_type": glove_type or "",
                            })
                    continue

                if name == "注意事項":
                    if not precaution_header_seen:
                        precaution_header_seen = True
                        continue
                    if not columns[0] and not columns[6]:
                        continue
                    catalogs["precautions"].append({
                        "id": _stable_id("prec", columns[0], columns[1], columns[6]),
                        "process": columns[0],
                        "category": columns[1],
                        "index_condition1": columns[2],
                        "index_condition2": columns[3],
                        "note_type": columns[4],
                        "note_code": columns[5],
                        "description": columns[6],
                    })
                    continue

                if name == "從哪裡":
                    value = columns[0]
                    if value and value not in {"从哪里"} and value not in from_seen:
                        catalogs["from_locations"].append({"id": _stable_id("from", value), "name": value})
                        from_seen.add(value)
                    continue

                if name == "哪裡":
                    value = columns[0]
                    if value and value not in {"哪里"} and value not in ref_seen:
                        catalogs["reference_points"].append({"id": _stable_id("ref", value), "name": value})
                        ref_seen.add(value)
                    continue

                if name == "到哪裡":
                    value = columns[0]
                    if value and value not in {"到哪里"} and value not in to_seen:
                        catalogs["to_locations"].append({"id": _stable_id("to", value), "name": value})
                        to_seen.add(value)
                    continue

                if name == "元件":
                    value = columns[0]
                    if value and value != "元件" and value not in component_seen:
                        catalogs["components"].append({
                            "id": _stable_id("comp", value),
                            "name_cn": value,
                            "name_en": value,
                            "category": "元件",
                        })
                        component_seen.add(value)
                    continue

                if name == "離子風扇與目標物綁定":
                    category = columns[0]
                    target = columns[1]
                    note = columns[2] or columns[3]
                    header_tokens = {"目標物", "離子風扇與目標物綁定", "分類"}
                    if (not category and not target) or category in header_tokens or target in header_tokens:
                        continue
                    key = (category or "未分類", target or "未命名", note or "")
                    if key in ionfan_seen:
                        continue
                    catalogs["ion_fan_bindings"].append({
                        "id": _stable_id("ionfan", *key),
                        "object_category": key[0],
                        "object_name": key[1],
                        "note": key[2]
                    })
                    ionfan_seen.add(key)
                    continue

                if name == "MI命名方式":
                    values = {idx: val for idx, val in enumerate(columns, start=1) if val}
                    if not mi_headers and len(values) >= 2:
                        mi_headers = values
                        for idx, label in mi_headers.items():
                            mi_meta[idx] = {
                                "field_label": label,
                                "slug": _slugify_label(label, f"field_{idx}"),
                                "position": idx,
                                "notes": [],
                                "options": [],
                                "examples": [],
                                "required": False
                            }
                        continue
                    if not any(columns):
                        continue
                    if not mi_headers:
                        if columns[0]:
                            general_mi_notes.append(columns[0])
                        continue
                    for idx, meta in mi_meta.items():
                        cell = columns[idx - 1] if idx - 1 < len(columns) else ""
                        if not cell:
                            continue
                        cell_clean = cell.replace("\n", " ").strip()
                        if not cell_clean:
                            continue
                        lowered = cell_clean.lower()
                        if "必填" in cell_clean:
                            meta["required"] = True
                        if "範例" in cell_clean or "example" in lowered or "mi 標準文件名稱" in lowered:
                            meta["examples"].append(cell_clean)
                        elif "下拉" in cell_clean or "選擇" in cell_clean or "all model" in lowered:
                            meta["options"].append(cell_clean)
                        else:
                            meta["notes"].append(cell_clean)
                    continue

                if name == "Level system 邏輯":
                    title = columns[0]
                    body = columns[1] if columns[1] else ""
                    if not title and not body:
                        continue
                    normalized_title = title or "Level System 說明"
                    normalized_body = body or title
                    key = (normalized_title, normalized_body)
                    if key in level_guideline_seen:
                        continue
                    level_guideline_seen.add(key)
                    catalogs["level_guidelines"].append({
                        "id": _stable_id("lvl-guide", normalized_title, normalized_body or str(len(catalogs["level_guidelines"]))),
                        "title": normalized_title,
                        "body": normalized_body
                    })
                    continue
    except Exception as exc:
        logger.warning("Failed to parse ddm_structure.csv: %s", exc)
        return catalogs

    # derive location catalog from across lists for compatibility
    location_names = {entry["name"] for key in ("from_locations", "to_locations", "reference_points") for entry in catalogs[key]}
    catalogs["location_library"] = [{"id": _stable_id("loc", name), "name": name} for name in sorted(location_names)]

    if mi_meta:
        if general_mi_notes:
            first_idx = sorted(mi_meta.keys())[0]
            mi_meta[first_idx]["notes"].extend(general_mi_notes)
        catalogs["mi_naming_rules"] = [
            {
                "id": _stable_id("mi", meta["field_label"], str(meta["position"])),
                "field_label": meta["field_label"],
                "slug": meta["slug"],
                "position": meta["position"],
                "description": "\n".join(meta.get("notes", [])),
                "options": meta.get("options", []),
                "example": "\n".join(meta.get("examples", [])),
                "required": meta.get("required", False)
            }
            for _, meta in sorted(mi_meta.items())
        ]

    # attach default glove type to object entries when possible
    glove_lookup: Dict[str, str] = {}
    for rule in catalogs["glove_rules"]:
        if rule.get("object_name") and rule["glove_type"]:
            glove_lookup.setdefault(rule["object_name"], rule["glove_type"])
        if rule.get("object_category") and rule["glove_type"]:
            glove_lookup.setdefault(rule["object_category"], rule["glove_type"])

    for obj in catalogs["objects"]:
        obj.setdefault("glove_type", glove_lookup.get(obj["name"]) or glove_lookup.get(obj["category"]))

    return catalogs


DDM_CATALOGS = load_ddm_structure_catalogs()
if DDM_CATALOGS["objects"]:
    OBJECT_LIBRARY = DDM_CATALOGS["objects"]
if DDM_CATALOGS["components"]:
    COMPONENT_LIBRARY = DDM_CATALOGS["components"]
if DDM_CATALOGS["from_locations"]:
    FROM_LOCATIONS = DDM_CATALOGS["from_locations"]
if DDM_CATALOGS["to_locations"]:
    TO_LOCATIONS = DDM_CATALOGS["to_locations"]
if DDM_CATALOGS["reference_points"]:
    REFERENCE_POINTS = DDM_CATALOGS["reference_points"]
if DDM_CATALOGS["glove_rules"]:
    GLOVE_RULES = DDM_CATALOGS["glove_rules"]
if DDM_CATALOGS["precautions"]:
    PRECAUTIONS = DDM_CATALOGS["precautions"]
if DDM_CATALOGS["location_library"]:
    LOCATION_LIBRARY = DDM_CATALOGS["location_library"]
if DDM_CATALOGS["ion_fan_bindings"]:
    ION_FAN_BINDINGS = DDM_CATALOGS["ion_fan_bindings"]
if DDM_CATALOGS["mi_naming_rules"]:
    MI_NAMING_RULES = DDM_CATALOGS["mi_naming_rules"]
if DDM_CATALOGS["level_guidelines"]:
    LEVEL_GUIDELINES = DDM_CATALOGS["level_guidelines"]

# ============================================
# Helper Utilities
# ============================================

def lookup_tmu_from_table(value: float, table: List[Dict[str, Any]], key: str) -> int:
    for row in table:
        if value <= row[key]:
            return row["tmu"]
    return table[-1]["tmu"]


def lookup_a_tmu(distance_cm: float) -> int:
    return lookup_tmu_from_table(distance_cm, A_INDEX_TABLE, "max_cm")


def lookup_b_tmu(angle_deg: float) -> int:
    return lookup_tmu_from_table(angle_deg, B_INDEX_TABLE, "max_angle")


def lookup_b_foot_tmu(distance_cm: float) -> int:
    return lookup_tmu_from_table(distance_cm, B_FOOT_INDEX_TABLE, "max_cm")


def lookup_m_distance_tmu(distance_cm: float) -> int:
    """Lookup M index TMU based on distance (cm)."""
    return lookup_tmu_from_table(distance_cm, M_INDEX_TABLE, "max_cm")


def lookup_tool_distance_tmu(distance_cm: float) -> int:
    """
    Lookup Tool Action TMU based on distance (cm).
    適用於: 理/穿/推/拉/貼附/去除/撕除/撕开/折/擦拭
    TMU 規則: <=2.5cm=3, <=10cm=6, <=25cm=10, <=45cm=16, <=75cm=24
    """
    return lookup_tmu_from_table(distance_cm, TOOL_DISTANCE_TABLE, "max_cm")


def lookup_m_hand_angle_tmu(angle_deg: float) -> int:
    """Lookup M index TMU based on hand angle (degrees)."""
    return lookup_tmu_from_table(angle_deg, M_HAND_ANGLE_TABLE, "max_angle")


def lookup_m_foot_tmu(distance_cm: float) -> int:
    """Lookup M index TMU based on foot distance (cm)."""
    return lookup_tmu_from_table(distance_cm, M_FOOT_DISTANCE_TABLE, "max_cm")


def lookup_m_rotation_tmu(turns: int, diameter_cm: float = 12.5) -> int:
    """Lookup M index TMU based on rotation turns and diameter."""
    size = "small" if diameter_cm <= 12.5 else "large"
    table = M_ROTATION_TABLE.get(size, M_ROTATION_TABLE["small"])
    turns = max(1, min(turns, 3))  # Clamp to 1-3
    return table.get(turns, table.get(max(table.keys())))


def calculate_m_tmu_max(
    distance_cm: Optional[float] = None,
    hand_angle_deg: Optional[float] = None,
    foot_cm: Optional[float] = None,
    rotation_turns: Optional[int] = None,
    rotation_diameter_cm: float = 12.5
) -> int:
    """
    Calculate M index TMU using max value logic.
    根據 MOST邏輯詳解版: 同時需要考量 Verb + 手度 + 腳步，但取最大值
    """
    tmu_values = []
    
    if distance_cm is not None and distance_cm > 0:
        tmu_values.append(lookup_m_distance_tmu(distance_cm))
    
    if hand_angle_deg is not None and hand_angle_deg > 0:
        tmu_values.append(lookup_m_hand_angle_tmu(hand_angle_deg))
    
    if foot_cm is not None and foot_cm > 0:
        tmu_values.append(lookup_m_foot_tmu(foot_cm))
    
    if rotation_turns is not None and rotation_turns > 0:
        tmu_values.append(lookup_m_rotation_tmu(rotation_turns, rotation_diameter_cm))
    
    return max(tmu_values) if tmu_values else 0


def calculate_p_with_modifiers(
    base_p_index: int,
    modifiers: Optional[List[str]] = None
) -> Tuple[int, int, List[str]]:
    """
    Calculate P index with additive modifiers.
    根據 MOST邏輯詳解版:
    - 對準(精度<4mm): +8 TMU
    - 插入: +8 TMU
    - 較難處理: +8 TMU (WI不顯示)
    - 卡合: +16 TMU
    - 施加壓力: +16 TMU (WI不顯示)
    
    Returns: (total_tmu, addon_tmu, display_modifiers_for_wi)
    """
    if not modifiers:
        return base_p_index, 0, []
    
    # Limit to max 2 modifiers
    selected_modifiers = modifiers[:2]
    addon_tmu = 0
    display_modifiers = []
    
    for mod in selected_modifiers:
        mod_lower = mod.lower() if isinstance(mod, str) else mod
        for key, config in P_MODIFIERS_TABLE.items():
            if key.lower() == mod_lower or mod_lower in key.lower():
                addon_tmu += config["tmu_addon"]
                if config["show_in_wi"]:
                    display_modifiers.append(key)
                break
    
    return base_p_index + addon_tmu, addon_tmu, display_modifiers


def determine_glove_requirement(
    object_name: Optional[str],
    object_category: Optional[str],
    action: Optional[str],
    object_repo: Optional[List[Dict[str, Any]]] = None,
    glove_repo: Optional[List[Dict[str, Any]]] = None
) -> Optional[str]:
    """Return glove requirement based on object category and action verb."""
    if not (object_name or object_category or action):
        return None
    object_source = object_repo or OBJECT_LIBRARY
    glove_source = glove_repo or GLOVE_RULES
    category = object_category
    if not category and object_name:
        for obj in object_source:
            if obj["name"] == object_name or obj["id"] == object_name:
                category = obj["category"]
                break
    for rule in glove_source:
        if (not category or rule["object_category"] == category) and (not action or rule["action"] == action):
            return rule["glove_type"]
    if category:
        for obj in object_source:
            if obj["category"] == category:
                return obj.get("glove_type")
    return None


def find_ion_fan_binding(
    object_name: Optional[str],
    object_category: Optional[str],
    repo: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """Return ion-fan binding entry that matches by name priority, fallback to category."""
    if not (object_name or object_category):
        return None
    source = repo or ION_FAN_BINDINGS
    obj_name = str(object_name).strip().lower() if object_name else ""
    obj_category = str(object_category).strip().lower() if object_category else ""
    # prioritize exact object name match
    if obj_name:
        for binding in source:
            if str(binding.get("object_name", "")).strip().lower() == obj_name:
                return binding
    # fallback to category match
    if obj_category:
        for binding in source:
            if str(binding.get("object_category", "")).strip().lower() == obj_category:
                return binding
    # try partial contains as last resort to avoid missing similar labels
    if obj_name:
        for binding in source:
            candidate = str(binding.get("object_name", "")).strip().lower()
            if candidate and obj_name in candidate:
                return binding
    return None


def validate_mi_naming_payload(
    fields: Optional[Dict[str, Any]],
    rules: List[Dict[str, Any]]
) -> Tuple[List[str], str]:
    if not fields:
        fields = {}
    normalized = {}
    for key, value in fields.items():
        if key is None:
            continue
        normalized[str(key).lower()] = value
    errors: List[str] = []
    name_parts: List[str] = []
    for rule in rules:
        label = rule.get("field_label") or rule.get("label") or "欄位"
        slug = (rule.get("slug") or _slugify_label(label)).lower()
        position = str(rule.get("position") or "")
        candidates = [
            slug,
            slug.replace("_", ""),
            label.lower(),
            label.replace(" ", "").lower(),
            position,
            f"field{position}"
        ]
        value = None
        for candidate in candidates:
            if not candidate:
                continue
            if candidate in fields:
                value = fields.get(candidate)
                if value:
                    break
            if candidate in normalized:
                value = normalized.get(candidate)
                if value:
                    break
        if value is None and label in fields:
            value = fields[label]
        if isinstance(value, str):
            value = value.strip()
        if rule.get("required") and not value:
            errors.append(f"{label} 為必填欄位")
        if value and ("ct" in slug or "sec" in slug or "秒" in label):
            try:
                float(str(value).replace("_", ""))
            except ValueError:
                errors.append(f"{label} 需為數值")
        formatted = str(value).strip() if value is not None else ""
        name_parts.append(formatted)
    suggestion = "_".join(part for part in name_parts if part)
    return errors, suggestion


def build_syntax_library_seed() -> List[Dict[str, Any]]:
    """Seed syntax library with MiniMOST action verbs plus legacy samples."""
    base_entries = [
        {"id": "syn-001", "action_verb": "GET", "code_most": "A", "parameter_range": "Distance < 30cm", "tmu_value": 1},
        {"id": "syn-002", "action_verb": "GET", "code_most": "A", "parameter_range": "Distance 30-60cm", "tmu_value": 3},
        {"id": "syn-003", "action_verb": "GET", "code_most": "A", "parameter_range": "Distance > 60cm", "tmu_value": 6},
        {"id": "syn-004", "action_verb": "PUT", "code_most": "P", "parameter_range": "Loose fit", "tmu_value": 1},
        {"id": "syn-005", "action_verb": "PUT", "code_most": "P", "parameter_range": "Tight fit", "tmu_value": 3},
        {"id": "syn-006", "action_verb": "GRASP", "code_most": "G", "parameter_range": "Simple grasp", "tmu_value": 1},
        {"id": "syn-007", "action_verb": "GRASP", "code_most": "G", "parameter_range": "Complex grasp", "tmu_value": 3},
        {"id": "syn-008", "action_verb": "MOVE", "code_most": "M", "parameter_range": "Controlled < 30cm", "tmu_value": 3},
        {"id": "syn-009", "action_verb": "MOVE", "code_most": "M", "parameter_range": "Controlled > 30cm", "tmu_value": 6},
        {"id": "syn-010", "action_verb": "PROCESS", "code_most": "X", "parameter_range": "Simple process", "tmu_value": 3},
        {"id": "syn-011", "action_verb": "PROCESS", "code_most": "X", "parameter_range": "Complex process", "tmu_value": 6},
        {"id": "syn-012", "action_verb": "ALIGN", "code_most": "I", "parameter_range": "Simple alignment", "tmu_value": 1},
        {"id": "syn-013", "action_verb": "ALIGN", "code_most": "I", "parameter_range": "Precise alignment", "tmu_value": 3},
    ]
    existing_verbs = {entry["action_verb"] for entry in base_entries}
    for verb, profile in ACTION_SKILL_MAPPING.items():
        if verb not in existing_verbs:
            base_entries.append({
                "id": f"syn-{uuid.uuid4().hex[:8]}",
                "action_verb": verb,
                "code_most": profile["code"],
                "parameter_range": profile["skill"],
                "tmu_value": profile.get("tmu_default", 1)
            })
            existing_verbs.add(verb)
    return base_entries


def parse_level_tag(tag: str) -> Dict[str, Any]:
    """Parse tags like main:1|sub1:1.1|nb:2 into structured info."""
    parts = tag.split("|") if tag else []
    parsed = []
    for part in parts:
        if ":" in part:
            key, value = part.split(":", 1)
            parsed.append({"key": key.strip(), "value": value.strip()})
    return parsed


def validate_level_sequence(tags: List[str]) -> List[str]:
    """Apply simple MiniMOST level-system validation rules."""
    errors = []
    nb_counter: Dict[str, int] = {}
    for tag in tags:
        parsed = parse_level_tag(tag)
        for entry in parsed:
            key = entry["key"].lower()
            if key == "main" and not entry["value"]:
                errors.append("main層需提供順序編碼")
            if key.startswith("sub") and not entry["value"].count("."):
                errors.append("sub層級需包含父層編碼 (例如 sub1:2.1)")
            if key == "nb":
                nb_key = entry["value"] or "default"
                nb_counter[nb_key] = nb_counter.get(nb_key, 0) + 1
                if nb_counter[nb_key] > 2:
                    errors.append(f"nb限制 {nb_key} 超過2次，需拆解動作")
    return errors


def derive_most_tmu_from_params(params: Dict[str, Any], action_profile: Optional[Dict[str, Any]] = None) -> int:
    """
    Derive TMU from MOST parameters with dynamic lookup support.
    
    支援的動態查表功能 (根據 action_profile 標記):
    - distance_lookup: 工具距離查表 (理/穿/推/拉/貼附/去除/撕除/撕开/折/擦拭)
    - angle_lookup: 手度角度查表
    - foot_lookup: 腳步距離查表
    - dynamic_time: X 動態時間計算 (TMU = 時間秒 / 0.036)
    - tmu_out_of_sight: I 視線外 TMU 值
    
    注意: MiniMOST index 值需要乘以 10 才是 TMU，但進階參數返回的已是 TMU 值。
    """
    if not params:
        return 0
    
    # tmu_from_index: 基本 index 值需要乘以 10
    # tmu_direct: 進階參數直接是 TMU 值
    tmu_from_index = 0
    tmu_direct = 0
    
    index_keys = {
        "A", "A1", "A2", "A3",
        "B", "B1", "B2",
        "G", "P", "M", "X", "I"
    }
    
    # 收集 M 進階參數的 TMU 值 (取最大值)
    m_tmu_values = []
    has_m_advanced = False
    
    # 處理 M 距離參數 (用於 distance_lookup 類型)
    if params.get("M_distance_cm") is not None and params.get("M_distance_cm") > 0:
        has_m_advanced = True
        distance_cm = float(params["M_distance_cm"])
        if action_profile and action_profile.get("distance_lookup"):
            # 工具動作使用 TOOL_DISTANCE_TABLE
            m_tmu_values.append(lookup_tool_distance_tmu(distance_cm))
        else:
            # 一般 M 動作使用 M_INDEX_TABLE
            m_tmu_values.append(lookup_m_distance_tmu(distance_cm))
    
    # 處理手度角度參數 (angle_lookup)
    if params.get("M_hand_angle") is not None and params.get("M_hand_angle") > 0:
        has_m_advanced = True
        angle_deg = float(params["M_hand_angle"])
        m_tmu_values.append(lookup_m_hand_angle_tmu(angle_deg))
    
    # 處理腳步距離參數 (foot_lookup)
    if params.get("M_foot_cm") is not None and params.get("M_foot_cm") > 0:
        has_m_advanced = True
        foot_cm = float(params["M_foot_cm"])
        m_tmu_values.append(lookup_m_foot_tmu(foot_cm))
    
    # 處理旋轉參數
    if params.get("M_rotation_turns") is not None and params.get("M_rotation_turns") > 0:
        has_m_advanced = True
        turns = int(params["M_rotation_turns"])
        diameter = float(params.get("M_rotation_diameter", 12.5))
        m_tmu_values.append(lookup_m_rotation_tmu(turns, diameter))
    
    # M 取最大值 (根據 MOST邏輯詳解版規則)
    if m_tmu_values:
        tmu_direct += max(m_tmu_values)
    
    # 處理 X 動態時間 (dynamic_time) - 直接返回 TMU 值
    has_x_dynamic = False
    if params.get("X_time_seconds") is not None and params.get("X_time_seconds") > 0:
        has_x_dynamic = True
        time_seconds = float(params["X_time_seconds"])
        x_tmu = int(round(time_seconds / 0.036))
        tmu_direct += x_tmu
    
    # 處理 P 加算修飾符 - 直接返回 TMU 值
    if params.get("P_addon") is not None and params.get("P_addon") > 0:
        tmu_direct += int(params["P_addon"])
    
    # 處理基本 index 值 (需要乘以 10)
    for key, value in params.items():
        if not isinstance(value, (int, float)):
            continue
        key_upper = key.upper()
        if key_upper in index_keys:
            # 如果使用了進階 M 參數，跳過基本 M index
            if key_upper == "M" and has_m_advanced:
                continue
            # 如果使用了動態 X 時間，跳過基本 X index
            if key_upper == "X" and has_x_dynamic:
                continue
            tmu_from_index += int(value)
    
    # 處理 A/B 距離角度參數 - 直接返回 TMU 值
    if params.get("A_dist_cm") is not None:
        tmu_direct += lookup_a_tmu(float(params["A_dist_cm"]))
    if params.get("B_angle_deg") is not None:
        tmu_direct += lookup_b_tmu(float(params["B_angle_deg"]))
    if params.get("foot_cm") is not None:
        tmu_direct += lookup_b_foot_tmu(float(params["foot_cm"]))
    
    # Total TMU calculation: (index value * 10) + direct TMU value
    return int(tmu_from_index * 10 + tmu_direct)


def generate_index_string(params: Dict[str, Any], seq_type: str, return_a_cm: float = 0) -> str:
    """Generate MiniMOST Index String like 'A1 B0 G1 A3 B0 P1 A0' (GENERAL) or 'A1 B0 G1 M6 X3 I1 A0' (CONTROLLED).
    
    MOST standard syntax:
    - GENERAL: A (from) -> B (from) -> G (gain) -> A (to) -> B (to) -> P (place) -> A (return)
    - CONTROLLED: A (from) -> B (from) -> G (gain) -> M (controlled move) -> X (machine time) -> I (inspect) -> A (return)
    """
    if not params:
        return ""
    
    def lookup_a_index(cm: float) -> int:
        """Look up A index based on distance in cm."""
        if cm <= 2.5: return 0
        if cm <= 5: return 1
        if cm <= 10: return 3
        if cm <= 20: return 6
        if cm <= 35: return 10
        if cm <= 60: return 16
        if cm <= 120: return 24
        return 32
    
    return_a_index = lookup_a_index(return_a_cm or 0)
    
    if seq_type == "CONTROLLED":
        A1 = params.get("A1") or params.get("A") or 0
        B1 = params.get("B1") or params.get("B") or 0
        G = params.get("G") or 0
        M = params.get("M") or 0
        X = params.get("X") or 0
        I = params.get("I") or 0
        A3 = params.get("A3") or return_a_index
        return f"A{A1} B{B1} G{G} M{M} X{X} I{I} A{A3}"
    
    # GENERAL Move: A1 B1 G A2 B2 P A3
    A1 = params.get("A1") or params.get("A") or 0
    B1 = params.get("B1") or params.get("B") or 0
    G = params.get("G") or 0
    A2 = params.get("A2") or 0
    B2 = params.get("B2") or 0
    P = params.get("P") or 0
    A3 = params.get("A3") or return_a_index
    return f"A{A1} B{B1} G{G} A{A2} B{B2} P{P} A{A3}"


def get_preposition(location_type: str, action_verb: Optional[str] = None) -> str:
    """Get appropriate preposition for Chinese sentence generation (internal use for Chinese context)."""
    from_preps = {
        "default": "從", "百寶箱": "從", "撿料架": "從", 
        "周轉車": "自", "站點Buffer": "由"
    }
    to_preps = {
        "default": "到", "主板": "至", "LCD": "置於", 
        "機箱": "放入", "垃圾桶": "丟入", "治具": "置於",
        "放": "放至", "插入": "插入", "卡合": "卡入", 
        "组": "組裝至", "锁附固定": "鎖於"
    }
    if location_type == "from":
        return from_preps.get(action_verb) or from_preps["default"]
    return to_preps.get(action_verb) or to_preps.get(location_type) or to_preps["default"]


def generate_chinese_sentence(
    action: str,
    obj: Optional[str],
    hand: Optional[str],
    from_location: Optional[str],
    to_location: Optional[str],
    frequency: int = 1,
    is_simo: bool = False
) -> str:
    """
    Generate auto Chinese SOP sentence with Natural Language Generation (NLG).
    Templates are selected based on action verb logic.
    """
    hand_label = hand or "右手"
    obj_text = obj or "物件"
    
    # Pre-processing for better flow
    if hand_label == "双手":
        verb_prefix = "雙手"
    elif hand_label == "左手":
        verb_prefix = "左手"
    else:
        verb_prefix = "右手"

    sentence = ""
    
    # 1. Action-Specific Templates
    if action in ["锁附固定", "鎖附", "Screwing"]:
        tool = "电动起子"  # context awareness needed for real case
        sentence = f"{verb_prefix}持{tool}對準{to_location or '螺孔'}鎖附{obj_text}"
    
    elif action in ["组", "Assembly", "組裝"]:
        sentence = f"{verb_prefix}將{obj_text}組裝至{to_location or '定位'}"
        
    elif action in ["G", "抓取", "Pick"]:
        from_part = f"自{from_location}" if from_location else ""
        sentence = f"{verb_prefix}{from_part}抓取{obj_text}"
        if to_location:
            sentence += f"並移動至{to_location}"

    elif action in ["P", "放置", "Place"]:
        sentence = f"{verb_prefix}將{obj_text}放置於{to_location or '定位'}"

    elif action in ["M", "Moves", "移動"]:
        sentence = f"{verb_prefix}移動{obj_text}至{to_location or '指定位置'}"

    elif action in ["I", "Inspect", "检查"]:
        sentence = f"{verb_prefix}目視檢查{obj_text}"
        if to_location:
            sentence += f" ({to_location})"

    else:
        # Generic Template
        from_prep = get_preposition("from", from_location)
        to_prep = get_preposition("to", action)
        
        parts = []
        parts.append(verb_prefix)
        if from_location:
            parts.append(f"{from_prep}{from_location}")
        
        # Verb + Object
        parts.append(action)
        parts.append(obj_text)
        
        if to_location:
            parts.append(f"{to_prep}{to_location}")
            
        sentence = "".join(parts)
    
    # Post-processing modifiers
    if is_simo:
        sentence = f"[同步] {sentence}"
    
    if frequency and frequency > 1:
        sentence = f"{sentence} ×{frequency}"
    
    return sentence.strip()


# ============================================
# In-Memory Database (Replace with PostgreSQL)
# ============================================

db = {
    "users": {
        "admin": {
            "id": "user-001",
            "username": "admin",
            "password": "admin123",
            "role": UserRole.MANAGER,
            "name": "Jason YY, Lin"
        },
        "engineer1": {
            "id": "user-002",
            "username": "engineer1",
            "password": "eng123",
            "role": UserRole.ENGINEER,
            "name": "Avery, Yeh"
        },
        "operator1": {
            "id": "user-003",
            "username": "operator1",
            "password": "op123",
            "role": UserRole.OPERATOR,
            "name": "Jason YY, Lin"
        }
    },
    "syntax_library": build_syntax_library_seed(),
    "component_library": deepcopy(COMPONENT_LIBRARY),
    "tool_library": [
        {"id": "tool-001", "name": "電動起子", "spec": "3±0.2 lbf.in", "bit": "δ,05,2#"},
        {"id": "tool-002", "name": "手套", "spec": "ESD", "bit": None},
        {"id": "tool-003", "name": "靜電氣槍", "spec": "Anti-static", "bit": None},
        {"id": "tool-004", "name": "吸塵器", "spec": "Clean room", "bit": None},
    ],
    "location_library": deepcopy(LOCATION_LIBRARY),
    "object_library": deepcopy(OBJECT_LIBRARY),
    "from_locations": deepcopy(FROM_LOCATIONS),
    "to_locations": deepcopy(TO_LOCATIONS),
    "reference_points": deepcopy(REFERENCE_POINTS),
    "precautions": deepcopy(PRECAUTIONS),
    "glove_rules": deepcopy(GLOVE_RULES),
    "level_system_templates": deepcopy(LEVEL_SYSTEM_TEMPLATES),
    "ion_fan_bindings": deepcopy(ION_FAN_BINDINGS),
    "mi_naming_rules": deepcopy(MI_NAMING_RULES),
    "level_guidelines": deepcopy(LEVEL_GUIDELINES),
    "employees": [
        {"id": "emp-001", "name": "張組長", "station_type": "Assembly", "skill_level": SkillLevel.EXPERT, "efficiency_factor": 1.2},
        {"id": "emp-002", "name": "李技術員", "station_type": "Assembly", "skill_level": SkillLevel.PROFICIENT, "efficiency_factor": 1.0},
        {"id": "emp-003", "name": "王工程師", "station_type": "Assembly", "skill_level": SkillLevel.EXPERT, "efficiency_factor": 1.1},
        {"id": "emp-004", "name": "陳小美", "station_type": "Assembly", "skill_level": SkillLevel.NOVICE, "efficiency_factor": 0.85},
        {"id": "emp-005", "name": "林大華", "station_type": "Assembly", "skill_level": SkillLevel.PROFICIENT, "efficiency_factor": 0.95},
    ],
    "projects": [
        {
            "id": "proj-001",
            "name": "K860G6-BASY",
            "family": "K860G6",
            "process_type": "BASY",
            "sku": "W*3558",
            "version": "1.3",
            "effective_date": "2025/8/7",
            "compliance": "RoHS2",
            "factory": "SQT"
        }
    ],
    "sop_versions": [
        {
            "id": "sop-001",
            "project_id": "proj-001",
            "version_no": "V1.0",
            "status": SOPStatus.PUBLISHED.value,
            "actions": [
                {
                    "id": "act-001",
                    "seq_type": "GENERAL",
                    "description": "將過完MVS站的主板放置在潔淨柵內的工作台上",
                    "tmu": 80,
                    "seconds": 2.88,
                    "params": {"A1": 6, "B1": 0, "G": 1, "A2": 3, "B2": 0, "P": 1, "A3": 0},
                    "station_id": "ST-3-1a",
                    "component": "主板",
                    "tool": "無",
                    "is_ctq": False,
                    "primary_action": "放",
                    "hand": "双手",
                    "object_category": "主板/MLB",
                    "glove_type": "兩只半指手套",
                    "level_tag": "main:1"
                },
                {
                    "id": "act-002",
                    "seq_type": "CONTROLLED",
                    "description": "掰開主板上DIMM槽，將過完MVS的DIMM按照配置要求放在DIMM槽內",
                    "tmu": 150,
                    "seconds": 5.4,
                    "params": {"A1": 3, "B1": 0, "G": 3, "M": 6, "X": 3, "I": 3, "A3": 0},
                    "station_id": "ST-3-1a",
                    "component": "DIMM",
                    "tool": "DIMM壓合治具",
                    "is_ctq": True,
                    "primary_action": "插入",
                    "hand": "双手",
                    "object_category": "記憶體",
                    "glove_type": "兩只半指手套",
                    "level_tag": "main:2|sub1:2.1"
                },
                {
                    "id": "act-003",
                    "seq_type": "CONTROLLED",
                    "description": "將假DIMM按照配置需求數量裝入主板DIMM槽內",
                    "tmu": 120,
                    "seconds": 4.32,
                    "params": {"A1": 3, "B1": 0, "G": 1, "M": 6, "X": 3, "I": 1, "A3": 0},
                    "station_id": "ST-3-1b",
                    "component": "假DIMM",
                    "tool": "DIMM壓合治具",
                    "is_ctq": False,
                    "primary_action": "卡合",
                    "hand": "双手",
                    "object_category": "記憶體",
                    "glove_type": "兩只半指手套",
                    "level_tag": "main:3|sub1:3.1"
                },
                {
                    "id": "act-004",
                    "seq_type": "CONTROLLED",
                    "description": "將兩顆拇指螺絲鎖緊，電動起子: 3±0.2 lbf.in，起子頭: δ,05,2#",
                    "tmu": 100,
                    "seconds": 3.6,
                    "params": {"A1": 1, "B1": 0, "G": 1, "M": 3, "X": 6, "I": 1, "A3": 0},
                    "station_id": "ST-4-1",
                    "component": "拇指螺絲",
                    "tool": "電動起子",
                    "is_ctq": True,
                    "primary_action": "锁附固定",
                    "hand": "右手",
                    "object_category": "標準件",
                    "glove_type": "兩只半指手套",
                    "level_tag": "main:4|sub1:4.1|nb:fasten"
                }
            ],
            "created_by": "user-002",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": "user-001",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "published_by": "user-001",
            "published_at": datetime.now(timezone.utc).isoformat()
        }
    ],
    "actions": [],
    "stations": [
        {"id": "ST-3-1a", "name": "第3-1站 (組裝DIMM站)", "operator": "EMP-001 (張組長)", "skill_level": 1.0},
        {"id": "ST-3-1b", "name": "第3-1站 (組裝假DIMM站)", "operator": "EMP-002 (李技術員)", "skill_level": 0.9},
        {"id": "ST-4-1", "name": "第4-1站 (組裝主板站)", "operator": "EMP-003 (王工程師)", "skill_level": 1.1}
    ],
    "audit_logs": [],
    "simulation_results": [],  # 產線平衡模擬結果歷史
    "level_entries": {}  # Level System entries: { project_id: [entry, ...] }
}

# ============================================
# JSON Persistence Functions
# ============================================

def save_db_to_json() -> bool:
    """Save current database state to JSON file."""
    if not AUTO_SAVE_ENABLED:
        return False
    try:
        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Extract only persistent keys
        persistent_data = {}
        for key in PERSISTENT_KEYS:
            if key in db:
                persistent_data[key] = db[key]
        
        # Add metadata
        persistent_data["_meta"] = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0"
        }
        
        # Write to file with pretty formatting
        with DB_JSON_PATH.open("w", encoding="utf-8") as f:
            json.dump(persistent_data, f, ensure_ascii=False, indent=2, default=str)
        
        logger.info(f"Database saved to {DB_JSON_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save database: {e}")
        return False


def load_db_from_json() -> bool:
    """Load database state from JSON file if exists."""
    if not DB_JSON_PATH.exists():
        logger.info("No persistent database file found, using defaults")
        return False
    
    try:
        with DB_JSON_PATH.open("r", encoding="utf-8") as f:
            persistent_data = json.load(f)
        
        # Remove metadata before merging
        meta = persistent_data.pop("_meta", {})
        logger.info(f"Loading database from {DB_JSON_PATH}, saved at: {meta.get('saved_at', 'unknown')}")
        
        # Merge loaded data into db
        for key in PERSISTENT_KEYS:
            if key in persistent_data:
                db[key] = persistent_data[key]
        
        logger.info(f"Database loaded successfully with {len(persistent_data)} collections")
        return True
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse database JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to load database: {e}")
        return False


def auto_save():
    """Convenience function to auto-save after modifications."""
    if AUTO_SAVE_ENABLED:
        save_db_to_json()


def reset_db_to_defaults():
    """Reset database to initial defaults and clear persistent file."""
    global db
    # Re-initialize db with defaults (this will be called from an endpoint)
    if DB_JSON_PATH.exists():
        DB_JSON_PATH.unlink()
        logger.info("Persistent database file deleted")
    return True


# Load persisted data on startup
load_db_from_json()

# ============================================
# Pydantic Models
# ============================================

# Auth Models
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

class UserInfo(BaseModel):
    id: str
    username: str
    role: UserRole
    name: str

# Master Data Models
class SyntaxEntry(BaseModel):
    id: Optional[str] = None
    action_verb: str
    code_most: str
    parameter_range: str
    tmu_value: int

class ComponentEntry(BaseModel):
    id: Optional[str] = None
    name_cn: str
    name_en: str
    category: str

class ToolEntry(BaseModel):
    id: Optional[str] = None
    name: str
    spec: str
    bit: Optional[str] = None

class LocationEntry(BaseModel):
    id: Optional[str] = None
    name: str

class EmployeeEntry(BaseModel):
    id: Optional[str] = None
    name: str
    station_type: str
    skill_level: SkillLevel
    efficiency_factor: float = Field(ge=0.5, le=1.5)


class GloveCheckRequest(BaseModel):
    object_name: Optional[str] = None
    object_category: Optional[str] = None
    action: Optional[str] = None


class GloveCheckResponse(BaseModel):
    glove_type: Optional[str]
    matched_rule_id: Optional[str] = None
    object_category: Optional[str] = None


class LevelNode(BaseModel):
    action_id: str
    tag: str


class LevelSystemValidateRequest(BaseModel):
    nodes: List[LevelNode]


class LevelSystemValidateResponse(BaseModel):
    is_valid: bool
    errors: List[str]


# Level System Entry Models (Full Feature Implementation)
class LevelEntryUpdate(BaseModel):
    """Level System entry for a single MI action"""
    action_id: str  # Reference to MOST action id
    difficulty_factor: float = Field(default=1.0, ge=0.5, le=3.0)  # 難度系數
    adjusted_ct: Optional[float] = None  # CT × difficulty_factor
    number_tag: Optional[str] = None  # 分割限制名稱 (nb)
    number_count: Optional[int] = Field(default=None, ge=1)  # 分割限制數量
    main_seq: Optional[str] = None  # Main sequence (主要順序分層)
    order_seq: Optional[str] = None  # Order sequence (次要順序分層 - 可拆分)
    cub_group: Optional[str] = None  # Cub group (次要固化分層 - 不可拆分)
    machine_count: int = Field(default=1, ge=1)  # 機器數
    operator_count: int = Field(default=1, ge=1)  # 人數
    status_label: Optional[str] = None  # 狀態 (以 | 分割, e.g., "大導熱管|小導熱管")
    sort_order: Optional[int] = Field(default=None, ge=0)  # 客製化排序順序


class LevelEntryResponse(BaseModel):
    """Level entry with full action context"""
    id: str
    action_id: str
    row_no: int
    description: str  # MI text from MOST
    ct_seconds: float  # Original CT
    frequency: int = 1
    difficulty_factor: float
    adjusted_ct: float  # Calculated: CT × difficulty_factor
    number_tag: Optional[str] = None
    number_count: Optional[int] = None
    main_seq: Optional[str] = None
    order_seq: Optional[str] = None
    cub_group: Optional[str] = None
    machine_count: int = 1
    operator_count: int = 1
    status_label: Optional[str] = None
    # Calculated fields for Cub time adjustment
    effective_cub_ct: Optional[float] = None  # For Cub: CT / machine_count or CT / operator_count
    sort_order: Optional[int] = None


class LevelSystemSaveRequest(BaseModel):
    """Request to save level system entries for a project"""
    project_id: str
    entries: List[LevelEntryUpdate]


class LevelSystemSyncRequest(BaseModel):
    """Request to sync MOST actions to Level System"""
    project_id: str
    sop_version_id: Optional[str] = None


class MINamingValidationRequest(BaseModel):
    fields: Dict[str, Optional[str]] = Field(default_factory=dict)


class MINamingValidationResponse(BaseModel):
    is_valid: bool
    errors: List[str]
    suggested_name: Optional[str] = None

# MOST Calculation Models
class OperatorTime(BaseModel):
    """Individual operator time for collaborative operations"""
    employee_id: Optional[str] = None
    individual_tmu: Optional[int] = None

class MOSTStep(BaseModel):
    action: str
    object: Optional[str] = None
    seq_type: str = "GENERAL"
    hand: Optional[str] = None
    object_category: Optional[str] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    reference_point: Optional[str] = None
    primary_action: Optional[str] = None
    glove_type: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    frequency: int = 1
    is_simo: bool = False
    return_a_cm: float = 0
    # Collaborative operation fields
    is_collaborative: bool = False
    operator_count: int = 1
    operators: List[OperatorTime] = Field(default_factory=list)

class MOSTCalculateRequest(BaseModel):
    steps: List[MOSTStep]

class MOSTBreakdown(BaseModel):
    action: str
    object: str
    tmu: int
    code: str
    seq_type: str
    hand: Optional[str] = None
    glove_type: Optional[str] = None
    object_category: Optional[str] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    frequency: int = 1
    is_simo: bool = False
    index_string: Optional[str] = None
    auto_sentence: Optional[str] = None
    # Collaborative operation fields
    is_collaborative: bool = False
    operator_count: int = 1
    effective_tmu: Optional[int] = None  # MAX(all operator TMUs) for line balance

class MOSTCalculateResponse(BaseModel):
    total_tmu: int
    total_seconds: float
    breakdown: List[MOSTBreakdown]
    simo_max_tmu: Optional[int] = None
    simo_seconds: Optional[float] = None
    # Collaborative summary
    collaborative_effective_tmu: Optional[int] = None  # Total effective TMU for line balance
    collaborative_effective_seconds: Optional[float] = None

# SOP Models
class SOPAction(BaseModel):
    id: Optional[str] = None
    seq_type: str  # GENERAL or CONTROLLED
    description: str
    tmu: int
    seconds: float
    params: Dict[str, int]
    station_id: str
    component: Optional[str] = None
    tool: Optional[str] = None
    image_url: Optional[str] = None
    is_ctq: bool = False
    primary_action: Optional[str] = None
    hand: Optional[str] = None
    object_category: Optional[str] = None
    glove_type: Optional[str] = None
    frequency: int = 1
    level_tag: Optional[str] = None
    is_simo: bool = False
    simo_group_id: Optional[str] = None

class SOPVersion(BaseModel):
    id: Optional[str] = None
    project_id: str
    version_no: str
    status: SOPStatus = SOPStatus.DRAFT
    actions: List[SOPAction] = []
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    published_by: Optional[str] = None
    published_at: Optional[str] = None

class SOPCreateRequest(BaseModel):
    project_id: str
    version_no: str
    actions: List[SOPAction] = []

class SOPUpdateStatusRequest(BaseModel):
    status: SOPStatus
    comment: Optional[str] = None

# Line Balance Models
class StationAssignment(BaseModel):
    id: str
    sop_ids: List[str] = []
    employee_id: str

class LineBalanceRequest(BaseModel):
    project_id: str
    stations: List[StationAssignment]
    takt_time: float

class StationResult(BaseModel):
    id: str
    name: str
    operator: str
    skill_level: SkillLevel
    efficiency_factor: float
    standard_time: float
    actual_time: float
    actions: List[dict]
    is_overloaded: bool
    required_gloves: List[str] = Field(default_factory=list)
    ctq_actions: List[str] = Field(default_factory=list)
    ion_fan_required: bool = False
    ion_fan_targets: List[str] = Field(default_factory=list)

class LineBalanceResponse(BaseModel):
    bottleneck_station: str
    cycle_time: float
    uph: int
    balance_rate: float
    alerts: List[str]
    station_results: List[StationResult]

# Action Reassignment (Drag & Drop)
class ActionReassignRequest(BaseModel):
    action_id: str
    from_station_id: str
    to_station_id: str

# Audit Models
class AuditLogEntry(BaseModel):
    id: str
    timestamp: str
    user_id: str
    user_name: str
    action: AuditAction
    entity_type: str
    entity_id: str
    old_value: Optional[Dict] = None
    new_value: Optional[Dict] = None
    description: str

# ============================================
# Authentication & Authorization
# ============================================

def create_token(user_data: dict) -> str:
    payload = {
        "sub": user_data["username"],
        "role": user_data["role"].value,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username not in db["users"]:
            raise HTTPException(status_code=401, detail="User not found")
        return db["users"][username]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_role(allowed_roles: List[UserRole]):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, current_user: dict = Depends(verify_token), **kwargs):
            if current_user["role"] not in allowed_roles:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Access denied. Required roles: {[r.value for r in allowed_roles]}"
                )
            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

def log_audit(user: dict, action: AuditAction, entity_type: str, entity_id: str, 
              description: str, old_value: dict = None, new_value: dict = None):
    """Log audit trail entry"""
    entry = {
        "id": f"audit-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user["id"],
        "user_name": user["name"],
        "action": action.value,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "old_value": old_value,
        "new_value": new_value,
        "description": description
    }
    db["audit_logs"].append(entry)
    # Auto-save after audit log (which means data was modified)
    auto_save()
    return entry

# ============================================
# Auth Endpoints
# ============================================

@app.post("/api/v1/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """User login endpoint"""
    user = db["users"].get(request.username)
    if not user or user["password"] != request.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_token(user)
    return TokenResponse(
        access_token=token,
        user={
            "id": user["id"],
            "username": user["username"],
            "role": user["role"].value,
            "name": user["name"]
        }
    )

@app.get("/api/v1/auth/me")
async def get_current_user(current_user: dict = Depends(verify_token)):
    """Get current user info"""
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "role": current_user["role"].value,
        "name": current_user["name"]
    }

# ============================================
# Master Data Management Endpoints
# ============================================

# Syntax Library
@app.get("/api/v1/master/syntax")
async def list_syntax(current_user: dict = Depends(verify_token)):
    """List all syntax entries"""
    return db["syntax_library"]

@app.post("/api/v1/master/syntax")
async def create_syntax(entry: SyntaxEntry, current_user: dict = Depends(verify_token)):
    """Create new syntax entry (Engineer/Manager only)"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify syntax library")
    
    entry_dict = entry.dict()
    entry_dict["id"] = f"syn-{uuid.uuid4().hex[:8]}"
    db["syntax_library"].append(entry_dict)
    
    log_audit(current_user, AuditAction.CREATE, "SyntaxLibrary", entry_dict["id"],
              f"Created syntax entry: {entry.action_verb} - {entry.code_most}",
              new_value=entry_dict)
    
    return entry_dict

@app.put("/api/v1/master/syntax/{syntax_id}")
async def update_syntax(syntax_id: str, entry: SyntaxEntry, current_user: dict = Depends(verify_token)):
    """Update syntax entry"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify syntax library")
    
    for i, s in enumerate(db["syntax_library"]):
        if s["id"] == syntax_id:
            old_value = s.copy()
            entry_dict = entry.dict()
            entry_dict["id"] = syntax_id
            db["syntax_library"][i] = entry_dict
            
            log_audit(current_user, AuditAction.UPDATE, "SyntaxLibrary", syntax_id,
                      f"Updated syntax entry: {entry.action_verb}",
                      old_value=old_value, new_value=entry_dict)
            
            return entry_dict
    
    raise HTTPException(status_code=404, detail="Syntax entry not found")

@app.delete("/api/v1/master/syntax/{syntax_id}")
async def delete_syntax(syntax_id: str, current_user: dict = Depends(verify_token)):
    """Delete syntax entry (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can delete syntax entries")
    
    for i, s in enumerate(db["syntax_library"]):
        if s["id"] == syntax_id:
            deleted = db["syntax_library"].pop(i)
            log_audit(current_user, AuditAction.DELETE, "SyntaxLibrary", syntax_id,
                      f"Deleted syntax entry: {deleted['action_verb']}",
                      old_value=deleted)
            return {"message": "Deleted successfully"}
    
    raise HTTPException(status_code=404, detail="Syntax entry not found")

# Component Library
@app.get("/api/v1/master/components")
async def list_components(current_user: dict = Depends(verify_token)):
    """List all components"""
    return db["component_library"]

@app.post("/api/v1/master/components")
async def create_component(entry: ComponentEntry, current_user: dict = Depends(verify_token)):
    """Create new component"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify components")
    
    entry_dict = entry.dict()
    entry_dict["id"] = f"comp-{uuid.uuid4().hex[:8]}"
    db["component_library"].append(entry_dict)
    
    log_audit(current_user, AuditAction.CREATE, "ComponentLibrary", entry_dict["id"],
              f"Created component: {entry.name_cn}", new_value=entry_dict)
    
    return entry_dict

@app.put("/api/v1/master/components/{component_id}")
async def update_component(component_id: str, entry: ComponentEntry, current_user: dict = Depends(verify_token)):
    """Update component"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify components")
    
    for i, c in enumerate(db["component_library"]):
        if c["id"] == component_id:
            old_value = c.copy()
            entry_dict = entry.dict()
            entry_dict["id"] = component_id
            db["component_library"][i] = entry_dict
            
            log_audit(current_user, AuditAction.UPDATE, "ComponentLibrary", component_id,
                      f"Updated component: {entry.name_cn}",
                      old_value=old_value, new_value=entry_dict)
            
            return entry_dict
    
    raise HTTPException(status_code=404, detail="Component not found")

    @app.delete("/api/v1/master/components/{component_id}")
    async def delete_component(component_id: str, current_user: dict = Depends(verify_token)):
        """Delete component (Manager only)"""
        if current_user["role"] != UserRole.MANAGER:
            raise HTTPException(status_code=403, detail="Only managers can delete components")
    
        for i, c in enumerate(db["component_library"]):
            if c["id"] == component_id:
                removed = db["component_library"].pop(i)
                log_audit(current_user, AuditAction.DELETE, "ComponentLibrary", component_id,
                          f"Deleted component: {removed['name_cn']}", old_value=removed)
                return {"message": "Deleted successfully"}
        raise HTTPException(status_code=404, detail="Component not found")

# Tool Library
@app.get("/api/v1/master/tools")
async def list_tools(current_user: dict = Depends(verify_token)):
    """List all tools"""
    return db["tool_library"]

@app.post("/api/v1/master/tools")
async def create_tool(entry: ToolEntry, current_user: dict = Depends(verify_token)):
    """Create new tool"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify tools")
    
    entry_dict = entry.dict()
    entry_dict["id"] = f"tool-{uuid.uuid4().hex[:8]}"
    db["tool_library"].append(entry_dict)
    
    log_audit(current_user, AuditAction.CREATE, "ToolLibrary", entry_dict["id"],
              f"Created tool: {entry.name}", new_value=entry_dict)
    
    return entry_dict

@app.put("/api/v1/master/tools/{tool_id}")
async def update_tool(tool_id: str, entry: ToolEntry, current_user: dict = Depends(verify_token)):
    """Update tool"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify tools")
    
    for i, t in enumerate(db["tool_library"]):
        if t["id"] == tool_id:
            old_value = t.copy()
            entry_dict = entry.dict()
            entry_dict["id"] = tool_id
            db["tool_library"][i] = entry_dict
            log_audit(current_user, AuditAction.UPDATE, "ToolLibrary", tool_id,
                      f"Updated tool: {entry.name}", old_value=old_value, new_value=entry_dict)
            return entry_dict
    raise HTTPException(status_code=404, detail="Tool not found")

@app.delete("/api/v1/master/tools/{tool_id}")
async def delete_tool(tool_id: str, current_user: dict = Depends(verify_token)):
    """Delete tool (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can delete tools")
    
    for i, t in enumerate(db["tool_library"]):
        if t["id"] == tool_id:
            removed = db["tool_library"].pop(i)
            log_audit(current_user, AuditAction.DELETE, "ToolLibrary", tool_id,
                      f"Deleted tool: {removed['name']}", old_value=removed)
            return {"message": "Deleted successfully"}
    raise HTTPException(status_code=404, detail="Tool not found")

# Location Library
@app.get("/api/v1/master/locations")
async def list_locations(current_user: dict = Depends(verify_token)):
    """List all locations"""
    return db["location_library"]

@app.post("/api/v1/master/locations")
async def create_location(entry: LocationEntry, current_user: dict = Depends(verify_token)):
    """Create new location"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify locations")
    
    entry_dict = entry.dict()
    entry_dict["id"] = f"loc-{uuid.uuid4().hex[:8]}"
    db["location_library"].append(entry_dict)
    
    log_audit(current_user, AuditAction.CREATE, "LocationLibrary", entry_dict["id"],
              f"Created location: {entry.name}", new_value=entry_dict)
    
    return entry_dict

@app.put("/api/v1/master/locations/{location_id}")
async def update_location(location_id: str, entry: LocationEntry, current_user: dict = Depends(verify_token)):
    """Update location"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify locations")
    
    for i, loc in enumerate(db["location_library"]):
        if loc["id"] == location_id:
            old_value = loc.copy()
            entry_dict = entry.dict()
            entry_dict["id"] = location_id
            db["location_library"][i] = entry_dict
            log_audit(current_user, AuditAction.UPDATE, "LocationLibrary", location_id,
                      f"Updated location: {entry.name}", old_value=old_value, new_value=entry_dict)
            return entry_dict
    raise HTTPException(status_code=404, detail="Location not found")

@app.delete("/api/v1/master/locations/{location_id}")
async def delete_location(location_id: str, current_user: dict = Depends(verify_token)):
    """Delete location (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can delete locations")
    
    for i, loc in enumerate(db["location_library"]):
        if loc["id"] == location_id:
            removed = db["location_library"].pop(i)
            log_audit(current_user, AuditAction.DELETE, "LocationLibrary", location_id,
                      f"Deleted location: {removed['name']}", old_value=removed)
            return {"message": "Deleted successfully"}
    raise HTTPException(status_code=404, detail="Location not found")


@app.get("/api/v1/master/objects")
async def list_objects(current_user: dict = Depends(verify_token)):
    """List MiniMOST object catalog"""
    return db["object_library"]


class ObjectCreate(BaseModel):
    name: str
    category: str
    sub_category: Optional[str] = None
    glove_type: Optional[str] = None
    ctq: bool = False


class ObjectUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    glove_type: Optional[str] = None
    ctq: Optional[bool] = None


@app.post("/api/v1/master/objects", status_code=201)
async def create_object(obj: ObjectCreate, current_user: dict = Depends(verify_token)):
    """Create a new object in the library"""
    new_id = f"obj-{uuid.uuid4().hex[:8]}"
    new_obj = {
        "id": new_id,
        "name": obj.name,
        "category": obj.category,
        "sub_category": obj.sub_category or obj.category,
        "glove_type": obj.glove_type or "一般作業手套",
        "ctq": obj.ctq
    }
    db["object_library"].append(new_obj)
    return new_obj


@app.put("/api/v1/master/objects/{object_id}")
async def update_object(object_id: str, obj: ObjectUpdate, current_user: dict = Depends(verify_token)):
    """Update an existing object"""
    for item in db["object_library"]:
        if item["id"] == object_id:
            if obj.name is not None:
                item["name"] = obj.name
            if obj.category is not None:
                item["category"] = obj.category
            if obj.sub_category is not None:
                item["sub_category"] = obj.sub_category
            if obj.glove_type is not None:
                item["glove_type"] = obj.glove_type
            if obj.ctq is not None:
                item["ctq"] = obj.ctq
            return item
    raise HTTPException(status_code=404, detail="Object not found")


@app.delete("/api/v1/master/objects/{object_id}", status_code=204)
async def delete_object(object_id: str, current_user: dict = Depends(verify_token)):
    """Delete an object from the library"""
    for i, item in enumerate(db["object_library"]):
        if item["id"] == object_id:
            db["object_library"].pop(i)
            return
    raise HTTPException(status_code=404, detail="Object not found")


@app.get("/api/v1/master/from-locations")
async def list_from_locations(current_user: dict = Depends(verify_token)):
    return db["from_locations"]


@app.get("/api/v1/master/to-locations")
async def list_to_locations(current_user: dict = Depends(verify_token)):
    return db["to_locations"]


@app.get("/api/v1/master/reference-points")
async def list_reference_points(current_user: dict = Depends(verify_token)):
    return db["reference_points"]


@app.get("/api/v1/master/precautions")
async def list_precautions(current_user: dict = Depends(verify_token)):
    return db["precautions"]


@app.get("/api/v1/master/glove-rules")
async def list_glove_rules(current_user: dict = Depends(verify_token)):
    return db["glove_rules"]


@app.get("/api/v1/master/ion-fan-bindings")
async def list_ion_fan_bindings(current_user: dict = Depends(verify_token)):
    return db["ion_fan_bindings"]


@app.get("/api/v1/master/mi-naming")
async def list_mi_naming_rules(current_user: dict = Depends(verify_token)):
    return db["mi_naming_rules"]


@app.post("/api/v1/mi-naming/validate", response_model=MINamingValidationResponse)
async def validate_mi_naming(request: MINamingValidationRequest, current_user: dict = Depends(verify_token)):
    errors, suggestion = validate_mi_naming_payload(request.fields, db["mi_naming_rules"])
    return MINamingValidationResponse(is_valid=len(errors) == 0, errors=errors, suggested_name=suggestion or None)


@app.post("/api/v1/gloves/check", response_model=GloveCheckResponse)
async def check_glove_requirement(request: GloveCheckRequest, current_user: dict = Depends(verify_token)):
    glove = determine_glove_requirement(
        request.object_name,
        request.object_category,
        request.action,
        db["object_library"],
        db["glove_rules"]
    )
    matched_rule = None
    for rule in db["glove_rules"]:
        if glove and rule["glove_type"] == glove:
            matched_rule = rule
            break
    return GloveCheckResponse(
        glove_type=glove,
        matched_rule_id=matched_rule["id"] if matched_rule else None,
        object_category=request.object_category
    )


@app.get("/api/v1/level-system/templates")
async def list_level_system_templates(current_user: dict = Depends(verify_token)):
    return db["level_system_templates"]


@app.get("/api/v1/level-system/guidelines")
async def list_level_guidelines(current_user: dict = Depends(verify_token)):
    return db["level_guidelines"]


@app.post("/api/v1/level-system/validate", response_model=LevelSystemValidateResponse)
async def validate_level_system(request: LevelSystemValidateRequest, current_user: dict = Depends(verify_token)):
    tags = [node.tag for node in request.nodes]
    errors = validate_level_sequence(tags)
    return LevelSystemValidateResponse(is_valid=len(errors) == 0, errors=errors)


# ============================================
# Level System Full Implementation APIs
# ============================================

@app.get("/api/v1/level-system/{project_id}")
async def get_level_system_entries(project_id: str, current_user: dict = Depends(verify_token)):
    """
    Get Level System entries for a project.
    Returns MI actions from MOST with their level system configuration.
    """
    # Find project
    project = None
    for p in db["projects"]:
        if p["id"] == project_id:
            project = p
            break
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Find SOP version for this project (prefer Draft, fallback to any)
    sop_actions = []
    target_sop = None
    
    # First try to find a Draft version
    for sop in db["sop_versions"]:
        if sop.get("project_id") == project_id:
            if sop.get("status") == SOPStatus.DRAFT.value:
                target_sop = sop
                break
    
    # If no Draft, use any version (prefer latest)
    if not target_sop:
        candidates = [s for s in db["sop_versions"] if s.get("project_id") == project_id]
        if candidates:
            target_sop = candidates[-1]  # Use the last one (most recently added)
    
    if target_sop:
        sop_actions = target_sop.get("actions", [])
    
    # Get existing level entries for this project
    level_entries = db.get("level_entries", {}).get(project_id, [])
    level_map = {e.get("action_id"): e for e in level_entries}
    
    # Build response with merged data
    entry_blocks = []
    for idx, action in enumerate(sop_actions):
        action_id = action.get("id")
        level_entry = level_map.get(action_id, {})
        
        ct_seconds = action.get("seconds", 0)
        difficulty_factor = level_entry.get("difficulty_factor", 1.0)
        adjusted_ct = round(ct_seconds * difficulty_factor, 2)
        frequency = action.get("frequency", 1)
        sort_key = level_entry.get("sort_order")
        if sort_key is None:
            sort_key = idx
        
        # Calculate effective Cub CT based on machine/operator count
        machine_count = level_entry.get("machine_count", 1)
        operator_count = level_entry.get("operator_count", 1)
        effective_cub_ct = None
        if level_entry.get("cub_group"):
            # For Cub entries, effective time = adjusted_ct / max(machine, operator)
            divisor = max(machine_count, operator_count)
            effective_cub_ct = round(adjusted_ct / divisor, 2) if divisor > 1 else adjusted_ct
        
        entry_blocks.append({
            "sort_key": sort_key,
            "data": {
                "id": level_entry.get("id", f"lvl-{action_id}"),
                "action_id": action_id,
                "description": action.get("description", ""),
                "ct_seconds": ct_seconds,
                "difficulty_factor": difficulty_factor,
                "adjusted_ct": adjusted_ct,
                "frequency": frequency,
                "number_tag": level_entry.get("number_tag"),
                "number_count": level_entry.get("number_count"),
                "main_seq": level_entry.get("main_seq"),
                "order_seq": level_entry.get("order_seq"),
                "cub_group": level_entry.get("cub_group"),
                "machine_count": machine_count,
                "operator_count": operator_count,
                "status_label": level_entry.get("status_label"),
                "effective_cub_ct": effective_cub_ct
            }
        })
    
    # Sort based on stored sort_order (fallback to SOP order)
    entry_blocks.sort(key=lambda block: block["sort_key"])
    result = []
    for new_idx, block in enumerate(entry_blocks):
        entry = block["data"]
        entry["row_no"] = new_idx + 1
        entry["sort_order"] = new_idx
        result.append(entry)
    
    return {
        "project_id": project_id,
        "project_name": project.get("name", ""),
        "entries": result,
        "total_count": len(result)
    }


@app.post("/api/v1/level-system/sync")
async def sync_level_system(request: LevelSystemSyncRequest, current_user: dict = Depends(verify_token)):
    """
    Sync MOST actions to Level System.
    Creates default level entries for any new actions from MOST/SOP.
    """
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify Level System")
    
    project_id = request.project_id
    
    # Find SOP version for this project (prefer Draft, fallback to any)
    sop_actions = []
    target_sop = None
    
    # First try to find a Draft version
    for sop in db["sop_versions"]:
        if sop.get("project_id") == project_id:
            if request.sop_version_id and sop.get("id") != request.sop_version_id:
                continue
            if sop.get("status") == SOPStatus.DRAFT.value:
                target_sop = sop
                break
    
    # If no Draft, use any version
    if not target_sop:
        for sop in db["sop_versions"]:
            if sop.get("project_id") == project_id:
                if request.sop_version_id and sop.get("id") != request.sop_version_id:
                    continue
                target_sop = sop
                break
    
    if target_sop:
        sop_actions = target_sop.get("actions", [])
    
    # If no SOP actions found, return empty result instead of error
    if not sop_actions:
        return {
            "message": "No SOP actions found for this project. Please save MOST steps to SOP first.",
            "project_id": project_id,
            "total_entries": 0,
            "synced": 0
        }
    
    # Initialize level_entries for project if not exists
    if project_id not in db.get("level_entries", {}):
        if "level_entries" not in db:
            db["level_entries"] = {}
        db["level_entries"][project_id] = []
    
    existing_entries = db["level_entries"][project_id]
    existing_action_ids = {e.get("action_id") for e in existing_entries}
    
    # Add new entries for actions not yet in level system
    new_count = 0
    for action in sop_actions:
        action_id = action.get("id")
        if action_id not in existing_action_ids:
            sort_order = len(existing_entries)
            new_entry = {
                "id": f"lvl-{uuid.uuid4().hex[:8]}",
                "action_id": action_id,
                "difficulty_factor": 1.0,
                "number_tag": None,
                "number_count": None,
                "main_seq": None,
                "order_seq": None,
                "cub_group": None,
                "machine_count": 1,
                "operator_count": 1,
                "status_label": None,
                "sort_order": sort_order
            }
            db["level_entries"][project_id].append(new_entry)
            new_count += 1
    
    auto_save()
    
    return {
        "message": f"Synced {new_count} new entries",
        "project_id": project_id,
        "total_entries": len(db["level_entries"][project_id])
    }


@app.post("/api/v1/level-system/save")
async def save_level_system(request: LevelSystemSaveRequest, current_user: dict = Depends(verify_token)):
    """
    Save Level System entries for a project.
    Updates existing entries or creates new ones.
    """
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify Level System")
    
    project_id = request.project_id
    
    # Initialize if needed
    if "level_entries" not in db:
        db["level_entries"] = {}
    if project_id not in db["level_entries"]:
        db["level_entries"][project_id] = []
    
    existing_entries = db["level_entries"][project_id]
    entry_map = {e.get("action_id"): e for e in existing_entries}
    
    updated_count = 0
    created_count = 0
    
    for entry_update in request.entries:
        action_id = entry_update.action_id
        
        if action_id in entry_map:
            # Update existing
            existing = entry_map[action_id]
            existing["difficulty_factor"] = entry_update.difficulty_factor
            existing["number_tag"] = entry_update.number_tag
            existing["number_count"] = entry_update.number_count
            existing["main_seq"] = entry_update.main_seq
            existing["order_seq"] = entry_update.order_seq
            existing["cub_group"] = entry_update.cub_group
            existing["machine_count"] = entry_update.machine_count
            existing["operator_count"] = entry_update.operator_count
            existing["status_label"] = entry_update.status_label
            if entry_update.sort_order is not None:
                existing["sort_order"] = entry_update.sort_order
            updated_count += 1
        else:
            # Create new
            sort_order = entry_update.sort_order
            if sort_order is None:
                sort_order = len(db["level_entries"][project_id])
            new_entry = {
                "id": f"lvl-{uuid.uuid4().hex[:8]}",
                "action_id": action_id,
                "difficulty_factor": entry_update.difficulty_factor,
                "number_tag": entry_update.number_tag,
                "number_count": entry_update.number_count,
                "main_seq": entry_update.main_seq,
                "order_seq": entry_update.order_seq,
                "cub_group": entry_update.cub_group,
                "machine_count": entry_update.machine_count,
                "operator_count": entry_update.operator_count,
                "status_label": entry_update.status_label,
                "sort_order": sort_order
            }
            db["level_entries"][project_id].append(new_entry)
            entry_map[action_id] = new_entry
            created_count += 1
    
    log_audit(
        current_user, AuditAction.UPDATE, "LevelSystem", project_id,
        f"Saved {updated_count} updated, {created_count} created level entries"
    )
    
    auto_save()
    
    return {
        "message": "Level System saved successfully",
        "project_id": project_id,
        "updated": updated_count,
        "created": created_count
    }


@app.post("/api/v1/level-system/generate-graph")
async def generate_level_graph(project_id: str = "", current_user: dict = Depends(verify_token)):
    """
    Generate precedence graph/matrix for Line Balance algorithm.
    Returns structured data for optimization engine.
    """
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    
    # Get level entries
    level_entries = db.get("level_entries", {}).get(project_id, [])
    
    # Find SOP actions
    sop_actions = []
    for sop in db["sop_versions"]:
        if sop.get("project_id") == project_id:
            sop_actions = sop.get("actions", [])
            break
    
    level_map = {e.get("action_id"): e for e in level_entries}
    
    # Build graph nodes
    nodes = []
    precedence_edges = []
    cub_groups = {}
    number_constraints = {}
    
    for idx, action in enumerate(sop_actions):
        action_id = action.get("id")
        level = level_map.get(action_id, {})
        
        ct = action.get("seconds", 0)
        diff_factor = level.get("difficulty_factor", 1.0)
        adjusted_ct = round(ct * diff_factor, 2)
        
        machine_count = level.get("machine_count", 1)
        operator_count = level.get("operator_count", 1)
        
        # Effective time for scheduling
        effective_ct = adjusted_ct / max(machine_count, operator_count) if level.get("cub_group") else adjusted_ct
        
        node = {
            "id": action_id,
            "row_no": idx + 1,
            "description": action.get("description", ""),
            "original_ct": ct,
            "difficulty_factor": diff_factor,
            "adjusted_ct": adjusted_ct,
            "effective_ct": round(effective_ct, 2),
            "main_seq": level.get("main_seq"),
            "order_seq": level.get("order_seq"),
            "cub_group": level.get("cub_group"),
            "number_tag": level.get("number_tag"),
            "number_count": level.get("number_count"),
            "machine_count": machine_count,
            "operator_count": operator_count,
            "status_label": level.get("status_label")
        }
        nodes.append(node)
        
        # Track Cub groups (must stay together)
        if level.get("cub_group"):
            cub_name = level.get("cub_group")
            if cub_name not in cub_groups:
                cub_groups[cub_name] = []
            cub_groups[cub_name].append(action_id)
        
        # Track Number constraints
        if level.get("number_tag"):
            nb_tag = level.get("number_tag")
            nb_count = level.get("number_count", 1)
            if nb_tag not in number_constraints:
                number_constraints[nb_tag] = {"limit": nb_count, "actions": []}
            number_constraints[nb_tag]["actions"].append(action_id)
    
    # Build precedence edges from Main sequence
    main_sorted = sorted(
        [(n, n.get("main_seq", "")) for n in nodes if n.get("main_seq")],
        key=lambda x: x[1]
    )
    for i in range(len(main_sorted) - 1):
        precedence_edges.append({
            "from": main_sorted[i][0]["id"],
            "to": main_sorted[i + 1][0]["id"],
            "type": "main"
        })
    
    return {
        "project_id": project_id,
        "nodes": nodes,
        "precedence_edges": precedence_edges,
        "cub_groups": cub_groups,
        "number_constraints": number_constraints,
        "total_adjusted_ct": round(sum(n["adjusted_ct"] for n in nodes), 2),
        "total_effective_ct": round(sum(n["effective_ct"] for n in nodes), 2)
    }


# Employee Skill Matrix
@app.get("/api/v1/master/employees")
async def list_employees(current_user: dict = Depends(verify_token)):
    """List all employees with skill matrix"""
    return db["employees"]

@app.post("/api/v1/master/employees")
async def create_employee(entry: EmployeeEntry, current_user: dict = Depends(verify_token)):
    """Create new employee"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify employee data")
    
    entry_dict = entry.dict()
    entry_dict["id"] = f"emp-{uuid.uuid4().hex[:8]}"
    entry_dict["skill_level"] = entry.skill_level.value
    db["employees"].append(entry_dict)
    
    log_audit(current_user, AuditAction.CREATE, "EmployeeSkillMatrix", entry_dict["id"],
              f"Created employee: {entry.name}", new_value=entry_dict)
    
    return entry_dict

@app.put("/api/v1/master/employees/{employee_id}")
async def update_employee(employee_id: str, entry: EmployeeEntry, current_user: dict = Depends(verify_token)):
    """Update employee skill matrix"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot modify employee data")
    
    for i, e in enumerate(db["employees"]):
        if e["id"] == employee_id:
            old_value = e.copy()
            entry_dict = entry.dict()
            entry_dict["id"] = employee_id
            entry_dict["skill_level"] = entry.skill_level.value
            db["employees"][i] = entry_dict
            
            log_audit(current_user, AuditAction.UPDATE, "EmployeeSkillMatrix", employee_id,
                      f"Updated employee: {entry.name}",
                      old_value=old_value, new_value=entry_dict)
            
            return entry_dict
    
    raise HTTPException(status_code=404, detail="Employee not found")

    @app.delete("/api/v1/master/employees/{employee_id}")
    async def delete_employee(employee_id: str, current_user: dict = Depends(verify_token)):
        """Delete employee (Manager only)"""
        if current_user["role"] != UserRole.MANAGER:
            raise HTTPException(status_code=403, detail="Only managers can delete employee data")
    
        for i, e in enumerate(db["employees"]):
            if e["id"] == employee_id:
                removed = db["employees"].pop(i)
                log_audit(current_user, AuditAction.DELETE, "EmployeeSkillMatrix", employee_id,
                          f"Deleted employee: {removed['name']}", old_value=removed)
                return {"message": "Deleted successfully"}
        raise HTTPException(status_code=404, detail="Employee not found")


# ============================================
# Station Management Endpoints
# ============================================

@app.get("/api/v1/master/stations")
async def list_stations(current_user: dict = Depends(verify_token)):
    """List all stations"""
    return db["stations"]


@app.post("/api/v1/master/stations")
async def create_station(request: dict, current_user: dict = Depends(verify_token)):
    """Create a new station (Engineer/Manager only)"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot create stations")
    
    station_id = request.get("id", "").strip()
    station_name = request.get("name", "").strip()
    employee_id = request.get("employee_id", "")
    
    if not station_id or not station_name:
        raise HTTPException(status_code=400, detail="Station ID and name are required")
    
    # Check for duplicate ID
    if any(s["id"] == station_id for s in db["stations"]):
        raise HTTPException(status_code=400, detail="Station ID already exists")
    
    # Validate employee exists if provided
    if employee_id and not any(e["id"] == employee_id for e in db["employees"]):
        raise HTTPException(status_code=400, detail="Employee not found")
    
    new_station = {
        "id": station_id,
        "name": station_name,
        "operator": employee_id,
        "skill_level": 1.0
    }
    
    # Get employee skill level if assigned
    if employee_id:
        emp = next((e for e in db["employees"] if e["id"] == employee_id), None)
        if emp:
            new_station["skill_level"] = emp.get("efficiency_factor", 1.0)
            new_station["operator"] = f"{employee_id} ({emp['name']})"
    
    db["stations"].append(new_station)
    save_db_to_json()
    
    log_audit(current_user, AuditAction.CREATE, "Station", station_id,
              f"Created station: {station_name}", new_value=new_station)
    
    return new_station


@app.put("/api/v1/master/stations/{station_id}")
async def update_station(station_id: str, request: dict, current_user: dict = Depends(verify_token)):
    """Update an existing station (Engineer/Manager only)"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot update stations")
    
    station_name = request.get("name", "").strip()
    employee_id = request.get("employee_id", "")
    
    if not station_name:
        raise HTTPException(status_code=400, detail="Station name is required")
    
    # Validate employee exists if provided
    if employee_id and not any(e["id"] == employee_id for e in db["employees"]):
        raise HTTPException(status_code=400, detail="Employee not found")
    
    for i, station in enumerate(db["stations"]):
        if station["id"] == station_id:
            old_value = dict(station)
            
            station["name"] = station_name
            if employee_id:
                emp = next((e for e in db["employees"] if e["id"] == employee_id), None)
                if emp:
                    station["operator"] = f"{employee_id} ({emp['name']})"
                    station["skill_level"] = emp.get("efficiency_factor", 1.0)
                else:
                    station["operator"] = employee_id
            
            db["stations"][i] = station
            save_db_to_json()
            
            log_audit(current_user, AuditAction.UPDATE, "Station", station_id,
                      f"Updated station: {station_name}", old_value=old_value, new_value=station)
            
            return station
    
    raise HTTPException(status_code=404, detail="Station not found")


@app.delete("/api/v1/master/stations/{station_id}")
async def delete_station(station_id: str, current_user: dict = Depends(verify_token)):
    """Delete a station (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can delete stations")
    
    for i, station in enumerate(db["stations"]):
        if station["id"] == station_id:
            removed = db["stations"].pop(i)
            save_db_to_json()
            
            log_audit(current_user, AuditAction.DELETE, "Station", station_id,
                      f"Deleted station: {removed['name']}", old_value=removed)
            
            return {"message": "Station deleted successfully", "deleted": removed}
    
    raise HTTPException(status_code=404, detail="Station not found")


# ============================================
# MOST Calculation Engine Endpoints
# ============================================

@app.post("/api/v1/most/calculate", response_model=MOSTCalculateResponse)
async def calculate_most(request: MOSTCalculateRequest, current_user: dict = Depends(verify_token)):
    """
    Calculate standard time using MOST methodology
    Validates syntax and returns calculated time
    Supports frequency multiplication and SIMO (simultaneous motion)
    """
    total_tmu = 0
    breakdown = []
    simo_groups: Dict[str, List[int]] = {}  # Track SIMO steps by hand
    
    for step in request.steps:
        search_verbs = [step.action]
        if step.primary_action and step.primary_action not in search_verbs:
            search_verbs.insert(0, step.primary_action)
        matching_syntax = None
        for syn in db["syntax_library"]:
            if any(syn["action_verb"].upper() == verb.upper() for verb in search_verbs):
                matching_syntax = syn
                break
        action_key = step.primary_action or step.action
        action_profile = ACTION_SKILL_MAPPING.get(action_key) or ACTION_SKILL_MAPPING.get(step.action)
        code = action_profile["code"] if action_profile else (matching_syntax["code_most"] if matching_syntax else "X")
        
        # 傳入 action_profile 以支援動態查表
        derived_tmu = derive_most_tmu_from_params(step.params, action_profile)
        
        fixed_tmu = None
        # 檢查 action_profile 中的 fixed 標記或 FIXED_TMU_VALUES
        if action_profile and action_profile.get("fixed"):
            fixed_tmu = action_profile.get("tmu_default")
        elif action_key in FIXED_TMU_VALUES:
            fixed_tmu = FIXED_TMU_VALUES[action_key]
        elif step.action in FIXED_TMU_VALUES:
            fixed_tmu = FIXED_TMU_VALUES[step.action]
        
        # 處理 I 動作的視線外 TMU
        # 邏輯說明：
        # 1. 如果前端選擇了 I index (I6/I10/I16/I24/I32)，該值已在 derived_tmu 中包含
        # 2. 只有當動作本身是 I 類動詞 (并检查/并确认/并对准/并对齐) 時，
        #    且沒有選擇 I index，才需要根據視線內外判斷使用哪個 TMU
        i_action_tmu = None
        if action_profile and action_profile.get("code") == "I":
            i_index = step.params.get("I", 0) if step.params else 0
            # 如果沒有選擇 I index (i_index == 0)，需要根據動詞的預設值
            if i_index == 0:
                out_of_sight = step.params.get("out_of_sight", False) if step.params else False
                if out_of_sight and action_profile.get("tmu_out_of_sight"):
                    i_action_tmu = action_profile.get("tmu_out_of_sight")
                else:
                    i_action_tmu = action_profile.get("tmu_default")
        
        # 處理 X 動作的動態時間計算
        x_dynamic_tmu = None
        if action_profile and action_profile.get("dynamic_time"):
            x_time = step.params.get("X_time_seconds") if step.params else None
            if x_time is not None and x_time > 0:
                x_dynamic_tmu = int(round(float(x_time) / 0.036))
        
        # TMU 優先級: 固定值 > 動態時間 > I動作TMU > 衍生值 > 預設值
        if fixed_tmu is not None:
            step_tmu = fixed_tmu
        elif x_dynamic_tmu is not None:
            step_tmu = x_dynamic_tmu
        elif i_action_tmu is not None:
            step_tmu = i_action_tmu
        elif derived_tmu > 0:
            step_tmu = derived_tmu
        elif action_profile:
            step_tmu = action_profile.get("tmu_default", 10)
        elif matching_syntax:
            step_tmu = matching_syntax["tmu_value"]
        else:
            step_tmu = 10
        
        # Apply frequency multiplier
        frequency = max(1, step.frequency or 1)
        step_tmu_with_freq = step_tmu * frequency
        
        object_category = step.object_category
        if not object_category and step.object:
            for obj in db["object_library"]:
                if obj["name"] == step.object or obj["id"] == step.object:
                    object_category = obj["category"]
                    break
        glove_type = step.glove_type or determine_glove_requirement(
            step.object,
            object_category,
            action_key,
            db["object_library"],
            db["glove_rules"]
        )
        
        # Generate Index String and Auto Sentence
        index_string = generate_index_string(step.params, step.seq_type, step.return_a_cm)
        auto_sentence = generate_chinese_sentence(
            action_key,
            step.object,
            step.hand,
            step.from_location,
            step.to_location,
            frequency,
            step.is_simo
        )
        
        # Track SIMO steps
        if step.is_simo:
            hand_key = step.hand or "双手"
            if hand_key not in simo_groups:
                simo_groups[hand_key] = []
            simo_groups[hand_key].append(step_tmu_with_freq)
        
        # Calculate effective TMU for collaborative operations
        # For line balance: take MAX of all operator times
        effective_tmu = step_tmu_with_freq
        is_collaborative = step.is_collaborative and step.operator_count > 1
        
        if is_collaborative and step.operators:
            operator_tmus = []
            for op in step.operators:
                if op.individual_tmu is not None and op.individual_tmu > 0:
                    operator_tmus.append(op.individual_tmu * frequency)
            if operator_tmus:
                # For line balance, effective time = MAX of all operator times
                effective_tmu = max(operator_tmus)
                # But for standard time calculation, we still track all individual times
        
        total_tmu += step_tmu_with_freq
        breakdown.append(MOSTBreakdown(
            action=action_key,
            object=step.object or step.object_category or "-",
            tmu=int(step_tmu_with_freq),
            code=code,
            seq_type=step.seq_type,
            hand=step.hand,
            glove_type=glove_type,
            object_category=object_category,
            from_location=step.from_location,
            to_location=step.to_location,
            frequency=frequency,
            is_simo=step.is_simo,
            index_string=index_string,
            auto_sentence=auto_sentence,
            is_collaborative=is_collaborative,
            operator_count=step.operator_count if is_collaborative else 1,
            effective_tmu=int(effective_tmu) if is_collaborative else None
        ))
    
    # Calculate SIMO max TMU (for Line Balance, take max of simultaneous operations)
    simo_max_tmu = None
    simo_seconds = None
    if simo_groups:
        # For SIMO, we take the max value from each hand group and sum them
        # But for true simultaneous motion, we only count the longer one
        simo_max_tmu = max(max(times) for times in simo_groups.values()) if any(simo_groups.values()) else None
        if simo_max_tmu:
            simo_seconds = round(simo_max_tmu * TMU_FACTOR, 2)
    
    # Calculate collaborative effective TMU for line balance
    collaborative_effective_tmu = 0
    for bd in breakdown:
        if bd.is_collaborative and bd.effective_tmu:
            collaborative_effective_tmu += bd.effective_tmu
        else:
            collaborative_effective_tmu += bd.tmu
    
    total_seconds = round(total_tmu * TMU_FACTOR, 2)
    collaborative_effective_seconds = round(collaborative_effective_tmu * TMU_FACTOR, 2) if collaborative_effective_tmu != total_tmu else None
    
    return MOSTCalculateResponse(
        total_tmu=total_tmu,
        total_seconds=total_seconds,
        breakdown=breakdown,
        simo_max_tmu=simo_max_tmu,
        simo_seconds=simo_seconds,
        collaborative_effective_tmu=collaborative_effective_tmu if collaborative_effective_tmu != total_tmu else None,
        collaborative_effective_seconds=collaborative_effective_seconds
    )

# ============================================
# SOP Management Endpoints
# ============================================

@app.get("/api/v1/sop/versions")
async def list_sop_versions(project_id: Optional[str] = None, current_user: dict = Depends(verify_token)):
    """List all SOP versions, optionally filtered by project"""
    versions = db["sop_versions"]
    if project_id:
        versions = [v for v in versions if v["project_id"] == project_id]
    
    # Operators can only see Published SOPs
    if current_user["role"] == UserRole.OPERATOR:
        versions = [v for v in versions if v["status"] == SOPStatus.PUBLISHED.value]
    
    return versions

@app.post("/api/v1/sop/versions")
async def create_sop_version(request: SOPCreateRequest, current_user: dict = Depends(verify_token)):
    """Create new SOP version (Engineer/Manager only)"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot create SOPs")
    
    version = {
        "id": f"sop-{uuid.uuid4().hex[:8]}",
        "project_id": request.project_id,
        "version_no": request.version_no,
        "status": SOPStatus.DRAFT.value,
        "actions": [a.dict() for a in request.actions],
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": None,
        "reviewed_at": None,
        "published_by": None,
        "published_at": None
    }
    
    # Assign IDs to actions
    for i, action in enumerate(version["actions"]):
        if not action.get("id"):
            action["id"] = f"act-{uuid.uuid4().hex[:8]}"
    
    db["sop_versions"].append(version)
    
    log_audit(current_user, AuditAction.CREATE, "SOPVersion", version["id"],
              f"Created SOP version: {request.version_no} for project {request.project_id}",
              new_value=version)
    
    return version

@app.get("/api/v1/sop/versions/{sop_id}")
async def get_sop_version(sop_id: str, current_user: dict = Depends(verify_token)):
    """Get specific SOP version"""
    for version in db["sop_versions"]:
        if version["id"] == sop_id:
            # Operators can only see Published
            if current_user["role"] == UserRole.OPERATOR and version["status"] != SOPStatus.PUBLISHED.value:
                raise HTTPException(status_code=403, detail="Access denied to unpublished SOP")
            return version
    
    raise HTTPException(status_code=404, detail="SOP version not found")

@app.put("/api/v1/sop/versions/{sop_id}/status")
async def update_sop_status(sop_id: str, request: SOPUpdateStatusRequest, current_user: dict = Depends(verify_token)):
    """Update SOP status (workflow: Draft -> Reviewed -> Published)"""
    for i, version in enumerate(db["sop_versions"]):
        if version["id"] == sop_id:
            old_status = version["status"]
            new_status = request.status.value
            
            # Validate workflow transitions
            if old_status == SOPStatus.DRAFT.value and new_status == SOPStatus.REVIEWED.value:
                # Engineer can submit for review, Manager can review
                if current_user["role"] == UserRole.OPERATOR:
                    raise HTTPException(status_code=403, detail="Operators cannot review SOPs")
                version["reviewed_by"] = current_user["id"]
                version["reviewed_at"] = datetime.now(timezone.utc).isoformat()
                
            elif old_status == SOPStatus.REVIEWED.value and new_status == SOPStatus.PUBLISHED.value:
                # Only Manager can publish
                if current_user["role"] != UserRole.MANAGER:
                    raise HTTPException(status_code=403, detail="Only managers can publish SOPs")
                version["published_by"] = current_user["id"]
                version["published_at"] = datetime.now(timezone.utc).isoformat()
                
            elif old_status == SOPStatus.DRAFT.value and new_status == SOPStatus.PUBLISHED.value:
                # Manager can fast-track publish
                if current_user["role"] != UserRole.MANAGER:
                    raise HTTPException(status_code=403, detail="Only managers can directly publish SOPs")
                version["reviewed_by"] = current_user["id"]
                version["reviewed_at"] = datetime.now(timezone.utc).isoformat()
                version["published_by"] = current_user["id"]
                version["published_at"] = datetime.now(timezone.utc).isoformat()
                
            else:
                raise HTTPException(status_code=400, detail=f"Invalid status transition: {old_status} -> {new_status}")
            
            version["status"] = new_status
            db["sop_versions"][i] = version
            
            log_audit(current_user, AuditAction.APPROVE if new_status == SOPStatus.REVIEWED.value else AuditAction.PUBLISH,
                      "SOPVersion", sop_id,
                      f"Changed SOP status: {old_status} -> {new_status}",
                      old_value={"status": old_status}, new_value={"status": new_status})
            
            return version
    
    raise HTTPException(status_code=404, detail="SOP version not found")

@app.put("/api/v1/sop/versions/{sop_id}/actions")
async def update_sop_actions(sop_id: str, actions: List[SOPAction], current_user: dict = Depends(verify_token)):
    """Update SOP actions (only for Draft status)"""
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot edit SOPs")
    
    for i, version in enumerate(db["sop_versions"]):
        if version["id"] == sop_id:
            if version["status"] != SOPStatus.DRAFT.value:
                raise HTTPException(status_code=400, detail="Cannot edit non-draft SOP")
            
            old_actions = version["actions"]
            new_actions = []
            for action in actions:
                action_dict = action.dict()
                if not action_dict.get("id"):
                    action_dict["id"] = f"act-{uuid.uuid4().hex[:8]}"
                new_actions.append(action_dict)
            
            version["actions"] = new_actions
            db["sop_versions"][i] = version
            
            log_audit(current_user, AuditAction.UPDATE, "SOPVersion", sop_id,
                      f"Updated SOP actions (count: {len(new_actions)})",
                      old_value={"action_count": len(old_actions)},
                      new_value={"action_count": len(new_actions)})
            
            return version
    
    raise HTTPException(status_code=404, detail="SOP version not found")

# ============================================
# Line Balance Simulation Endpoints
# ============================================

@app.post("/api/v1/simulation/line-balance", response_model=LineBalanceResponse)
async def simulate_line_balance(request: LineBalanceRequest, current_user: dict = Depends(verify_token)):
    """
    Simulates line output based on configuration and staff
    Calculates bottleneck, balance rate, and generates alerts
    """
    station_results = []
    alerts = []
    
    # Get project's SOP actions
    project_sops = [s for s in db["sop_versions"] if s["project_id"] == request.project_id]
    all_actions = []
    for sop in project_sops:
        all_actions.extend(sop.get("actions", []))
    
    max_time = 0
    total_time = 0
    
    for station_config in request.stations:
        # Find employee
        employee = None
        for emp in db["employees"]:
            if emp["id"] == station_config.employee_id:
                employee = emp
                break
        
        if not employee:
            alerts.append(f"Employee {station_config.employee_id} not found")
            continue
        
        emp_skill_value = employee.get("skill_level", SkillLevel.PROFICIENT)
        if isinstance(emp_skill_value, str):
            emp_skill_enum = SkillLevel(emp_skill_value)
        else:
            emp_skill_enum = emp_skill_value
        
        # Calculate station time
        station_actions = [deepcopy(a) for a in all_actions if a.get("station_id") == station_config.id]
        standard_time = sum(a.get("seconds", 0) for a in station_actions)
        station_gloves = set()
        ctq_action_ids: set[str] = set()
        station_ion_targets: set[str] = set()
        for action in station_actions:
            primary = action.get("primary_action") or action.get("action")
            action_profile = ACTION_SKILL_MAPPING.get(primary)
            if action_profile and action_profile.get("ctq") and emp_skill_enum == SkillLevel.NOVICE:
                alerts.append(
                    f"CTQ skill mismatch at {station_config.id}: {employee['name']} ({emp_skill_enum.value}) handling {primary}"
                )
                ctq_action_ids.add(action["id"])
            elif action.get("is_ctq"):
                ctq_action_ids.add(action["id"])
            glove = action.get("glove_type") or determine_glove_requirement(
                action.get("component"),
                action.get("object_category"),
                primary,
                db["object_library"],
                db["glove_rules"]
            )
            if glove:
                station_gloves.add(glove)
                action["recommended_glove"] = glove
            if action_profile and action_profile.get("ctq") and not glove:
                alerts.append(f"Glove missing for CTQ action {primary} at {station_config.id}")

            ion_binding = find_ion_fan_binding(
                action.get("component") or action.get("object"),
                action.get("object_category"),
                db.get("ion_fan_bindings")
            )
            if ion_binding:
                ion_note = ion_binding.get("note") or "需啟用離子風扇"
                action["ion_fan_required"] = True
                action["ion_fan_note"] = ion_note
                target_label = action.get("component") or action.get("object_category") or ion_binding.get("object_name") or ion_binding.get("object_category")
                if target_label:
                    decorated = f"{target_label} - {ion_note}" if ion_note else target_label
                    station_ion_targets.add(decorated)
        
        # Apply skill factor
        efficiency = employee.get("efficiency_factor", 1.0)
        actual_time = standard_time / efficiency if efficiency > 0 else standard_time
        
        is_overloaded = actual_time > request.takt_time
        
        if is_overloaded:
            skill_level = employee.get("skill_level", "Proficient")
            alerts.append(f"Station {station_config.id}: Overloaded by {(actual_time - request.takt_time):.2f}s (Employee skill: {skill_level})")
        
        # Check skill mismatch
        for action in station_actions:
            if action.get("is_ctq", False) and emp_skill_enum == SkillLevel.NOVICE:
                alerts.append(f"Warning: {employee['name']} (Novice) assigned to CTQ operation at {station_config.id}")
                ctq_action_ids.add(action["id"])

        if station_ion_targets:
            alerts.append(
                f"Station {station_config.id}: 離子風扇需求 - {', '.join(sorted(station_ion_targets))}"
            )
        
        station_result = StationResult(
            id=station_config.id,
            name=f"Station {station_config.id}",
            operator=employee["name"],
            skill_level=emp_skill_enum,
            efficiency_factor=efficiency,
            standard_time=round(standard_time, 2),
            actual_time=round(actual_time, 2),
            actions=[
                {
                    "id": a["id"],
                    "description": a["description"],
                    "seconds": a["seconds"],
                    "recommended_glove": a.get("recommended_glove"),
                    "primary_action": a.get("primary_action"),
                    "ion_fan_required": a.get("ion_fan_required", False),
                    "ion_fan_note": a.get("ion_fan_note")
                }
                for a in station_actions
            ],
            is_overloaded=is_overloaded,
            required_gloves=sorted(station_gloves),
            ctq_actions=sorted(ctq_action_ids),
            ion_fan_required=bool(station_ion_targets),
            ion_fan_targets=sorted(station_ion_targets)
        )
        
        station_results.append(station_result)
        total_time += actual_time
        max_time = max(max_time, actual_time)
    
    # Calculate metrics
    num_stations = len(station_results)
    balance_rate = (total_time / (max_time * num_stations)) if max_time > 0 and num_stations > 0 else 0
    cycle_time = max_time
    uph = int(3600 / cycle_time) if cycle_time > 0 else 0
    
    # Find bottleneck
    bottleneck = max(station_results, key=lambda x: x.actual_time) if station_results else None
    
    # Save simulation result to history
    sim_result = {
        "id": f"sim-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_id": request.project_id,
        "takt_time": request.takt_time,
        "cycle_time": round(cycle_time, 2),
        "uph": uph,
        "balance_rate": round(balance_rate, 2),
        "num_stations": num_stations,
        "bottleneck_station": bottleneck.id if bottleneck else "N/A",
        "total_time": round(total_time, 2),
        "alerts_count": len(alerts),
        "created_by": current_user.get("name", "unknown"),
        "station_summary": [
            {
                "id": s.id,
                "operator": s.operator,
                "actual_time": s.actual_time,
                "is_overloaded": s.is_overloaded
            }
            for s in station_results
        ]
    }
    db["simulation_results"].append(sim_result)
    
    # Keep only last 50 results to prevent file bloat
    MAX_SIMULATION_RESULTS = 50
    if len(db["simulation_results"]) > MAX_SIMULATION_RESULTS:
        db["simulation_results"] = db["simulation_results"][-MAX_SIMULATION_RESULTS:]
    
    auto_save()
    
    return LineBalanceResponse(
        bottleneck_station=bottleneck.id if bottleneck else "N/A",
        cycle_time=round(cycle_time, 2),
        uph=uph,
        balance_rate=round(balance_rate, 2),
        alerts=alerts,
        station_results=station_results
    )

@app.post("/api/v1/simulation/reassign-action")
async def reassign_action(request: ActionReassignRequest, current_user: dict = Depends(verify_token)):
    """
    Reassign an action from one station to another (Drag & Drop)
    Only Engineers and Managers can reassign
    """
    if current_user["role"] == UserRole.OPERATOR:
        raise HTTPException(status_code=403, detail="Operators cannot reassign actions")
    
    # Find the action in all SOP versions
    for sop in db["sop_versions"]:
        for action in sop["actions"]:
            if action["id"] == request.action_id:
                if action["station_id"] != request.from_station_id:
                    raise HTTPException(status_code=400, detail="Action not at specified source station")
                
                old_station = action["station_id"]
                action["station_id"] = request.to_station_id
                
                log_audit(current_user, AuditAction.UPDATE, "ActionAssignment", request.action_id,
                          f"Reassigned action from {old_station} to {request.to_station_id}",
                          old_value={"station_id": old_station},
                          new_value={"station_id": request.to_station_id})
                
                return {"message": "Action reassigned successfully", "action": action}
    
    raise HTTPException(status_code=404, detail="Action not found")

# ============================================
# Simulation History Endpoints
# ============================================

@app.get("/api/v1/simulation/history")
async def get_simulation_history(
    project_id: Optional[str] = None,
    limit: int = 20,
    current_user: dict = Depends(verify_token)
):
    """Get simulation results history"""
    results = db.get("simulation_results", [])
    
    if project_id:
        results = [r for r in results if r.get("project_id") == project_id]
    
    # Sort by timestamp descending (newest first)
    results = sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)
    
    return {
        "total": len(results),
        "results": results[:limit]
    }


@app.get("/api/v1/simulation/history/{sim_id}")
async def get_simulation_detail(sim_id: str, current_user: dict = Depends(verify_token)):
    """Get a single simulation result by ID"""
    for result in db.get("simulation_results", []):
        if result.get("id") == sim_id:
            return result
    raise HTTPException(status_code=404, detail="Simulation result not found")


@app.delete("/api/v1/simulation/history/{sim_id}")
async def delete_simulation_result(sim_id: str, current_user: dict = Depends(verify_token)):
    """Delete a simulation result (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can delete simulation results")
    
    results = db.get("simulation_results", [])
    for i, result in enumerate(results):
        if result.get("id") == sim_id:
            deleted = results.pop(i)
            auto_save()
            return {"message": "Simulation result deleted", "id": sim_id}
    
    raise HTTPException(status_code=404, detail="Simulation result not found")


@app.delete("/api/v1/simulation/history")
async def clear_simulation_history(current_user: dict = Depends(verify_token)):
    """Clear all simulation results (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can clear simulation history")
    
    count = len(db.get("simulation_results", []))
    db["simulation_results"] = []
    auto_save()
    return {"message": f"Cleared {count} simulation results"}


# ============================================
# Audit Trail Endpoints
# ============================================

@app.get("/api/v1/audit/logs")
async def get_audit_logs(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(verify_token)
):
    """Get audit logs (Manager only for full access)"""
    if current_user["role"] != UserRole.MANAGER:
        # Non-managers can only see their own actions
        user_id = current_user["id"]
    
    logs = db["audit_logs"]
    
    if entity_type:
        logs = [l for l in logs if l["entity_type"] == entity_type]
    if entity_id:
        logs = [l for l in logs if l["entity_id"] == entity_id]
    if user_id:
        logs = [l for l in logs if l["user_id"] == user_id]
    
    # Sort by timestamp descending
    logs = sorted(logs, key=lambda x: x["timestamp"], reverse=True)
    
    return logs[:limit]

# ============================================
# Project Endpoints
# ============================================

@app.get("/api/v1/projects")
async def list_projects(current_user: dict = Depends(verify_token)):
    """List all projects"""
    return db["projects"]

@app.get("/api/v1/projects/{project_id}")
async def get_project(project_id: str, current_user: dict = Depends(verify_token)):
    """Get project details"""
    for project in db["projects"]:
        if project["id"] == project_id:
            return project
    raise HTTPException(status_code=404, detail="Project not found")

# ============================================
# Agentic Workflow Endpoints (Stateful Multi-Turn)
# ============================================

from agent_router_poc import (
    AuthContext as AgentAuthContext,
    run_agent_workflow,
    resume_after_approval,
)


class AgentQueryRequest(BaseModel):
    """Request body for the stateful agent query endpoint."""
    query:      str            = Field(..., min_length=1, max_length=4096,
                                       description="Natural-language query for the agent.")
    session_id: Optional[str] = Field(default=None,
                                       description="Existing session UUID for multi-turn conversations. "
                                                   "Omit to start a fresh session.")


@app.post("/api/v1/agent/query")
async def agent_query(
    request:     AgentQueryRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: dict = Depends(verify_token),
):
    """
    Stateful multi-turn agent endpoint.

    Pass ``session_id`` from a previous response to continue the same
    conversation.  Omit (or send ``null``) to start a new session.
    The response always contains the ``session_id`` to use for the next turn.

    When the agent encounters a SENSITIVE tool (e.g. ``run_simulation``),
    the response will have ``status="PENDING_APPROVAL"`` and an ``action_id``.
    Call ``POST /api/v1/agent/approve/{session_id}`` with that ``action_id``
    to authorise execution.
    """
    auth = AgentAuthContext(
        user_id   = current_user["user_id"],
        role      = current_user["role"],
        jwt_token = credentials.credentials,
    )
    result = await run_agent_workflow(
        user_query = request.query,
        auth       = auth,
        session_id = request.session_id,
    )
    return result


class ApproveActionRequest(BaseModel):
    """Request body for the HITL approval endpoint."""
    action_id: str = Field(
        ...,
        description="The action_id returned in the PENDING_APPROVAL response.",
        min_length=32,
        max_length=64,
    )


@app.post("/api/v1/agent/approve/{session_id}")
async def approve_agent_action(
    session_id:   str,
    request:      ApproveActionRequest,
    credentials:  HTTPAuthorizationCredentials = Depends(security),
    current_user: dict = Depends(verify_token),
):
    """
    Human-in-the-Loop (HITL) approval endpoint.

    Validates ``action_id`` against the stored PendingAction for the given
    session, runs ToolGuard pre-flight security checks, executes the approved
    tool, and returns the result.

    Only users with the ``Manager`` role or above may approve SENSITIVE actions.
    A ``TOOL_APPROVED`` security audit event is emitted on success.
    """
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may approve agent actions.",
        )

    try:
        result = await resume_after_approval(
            session_id  = session_id,
            action_id   = request.action_id,
            approver_id = current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return result


@app.delete("/api/v1/agent/session/{session_id}")
async def delete_agent_session(
    session_id:   str,
    current_user: dict = Depends(verify_token),
):
    """Delete a stored agent session (GDPR / cleanup)."""
    from memory_store import get_memory_store
    store = get_memory_store()
    await store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}


# ============================================
# Mission Control Dashboard — Agent Observability API
# ============================================

from fastapi.responses import StreamingResponse as _StreamingResponse
import asyncio as _asyncio
from collections import defaultdict as _defaultdict


def _sse_verify_token(token: Optional[str] = None) -> dict:
    """
    Lightweight token check for SSE endpoints where the browser cannot set
    Authorization headers.  Accepts the raw JWT via ``?token=`` query param.
    Raises 401 on failure to prevent unauthenticated span exposure.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token required")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token invalid")


@app.get(
    "/api/v1/agent/stream/{session_id}",
    summary="SSE: real-time agent span stream for a session",
    tags=["Mission Control"],
)
async def stream_agent_spans(
    session_id: str,
    token: Optional[str] = None,
):
    """
    Server-Sent Events stream of :class:`AgentSpanRecord` objects for the
    given ``session_id`` (== trace_id).

    Because browsers cannot send custom ``Authorization`` headers with the
    native ``EventSource`` API, authentication is performed via the ``?token=``
    query parameter.

    The stream first replays all spans currently in the ring buffer that belong
    to ``session_id``, then pushes new spans in real-time as the agent runs.
    A keep-alive ``: ping`` comment is sent every 30 s to prevent proxy timeouts.

    Clients should handle ``event: done`` to detect end-of-session.
    """
    _sse_verify_token(token)

    from telemetry import subscribe_to_trace, unsubscribe_from_trace, get_trace_spans

    async def _event_generator():
        # 1. Replay historical spans already in the buffer for this trace.
        historical = get_trace_spans(session_id)
        for span in historical:
            yield f"data: {span.to_json()}\n\n"

        # 2. Subscribe for live spans going forward.
        queue = await subscribe_to_trace(session_id)
        try:
            while True:
                try:
                    span = await _asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {span.to_json()}\n\n"
                except _asyncio.TimeoutError:
                    # Keep-alive comment — prevents proxy / load-balancer timeout.
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            await unsubscribe_from_trace(session_id, queue)

    return _StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",   # Disable nginx buffering for SSE
        },
    )


@app.get(
    "/api/v1/agent/pending",
    summary="List all sessions with a PENDING HITL approval request",
    tags=["Mission Control"],
)
async def list_pending_actions(
    current_user: dict = Depends(verify_token),
):
    """
    Return every session that currently has an unresolved HITL
    ``PENDING_APPROVAL`` action.  The dashboard polls this endpoint to
    populate the Action Center gallery.
    """
    from memory_store import get_memory_store
    store = get_memory_store()
    pending = await store.list_pending_actions()
    return {"pending_actions": pending, "count": len(pending)}


class RejectActionRequest(BaseModel):
    """Request body for rejecting a HITL pending action."""
    action_id: str = Field(
        ...,
        description="The action_id returned in the PENDING_APPROVAL response.",
        min_length=32,
        max_length=64,
    )
    correction_directive: Optional[str] = Field(
        default=None,
        description=(
            "Optional free-text rule the expert wants to inject into the "
            "agent's future system prompts (e.g. 'Never slow down Machine 3 "
            "during peak hours — route overflow to Machine 2 instead')."
        ),
        max_length=2000,
    )
    agent_target: Optional[str] = Field(
        default=None,
        description=(
            "Agent the correction targets: 'CostOptimizationAgent', "
            "'QualityAndTimeAgent', 'ConsensusJudgeAgent', or 'GLOBAL'. "
            "Defaults to 'GLOBAL' when correction_directive is provided "
            "but agent_target is omitted."
        ),
        max_length=64,
    )


@app.post(
    "/api/v1/agent/reject/{session_id}",
    summary="Reject a HITL pending action for a session",
    tags=["Mission Control"],
)
async def reject_agent_action(
    session_id:   str,
    request:      RejectActionRequest,
    current_user: dict = Depends(verify_token),
):
    """
    Mark a ``PENDING`` action as ``REJECTED`` without executing the tool.

    Only Engineers and Managers may reject SENSITIVE actions.
    A ``TOOL_REJECTED`` event is logged for audit purposes.
    """
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may reject agent actions.",
        )

    from memory_store import get_memory_store, PendingActionStatus
    store = get_memory_store()
    session_obj = await store.load(session_id)
    if session_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if session_obj.pending_action is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No pending action for this session.")
    if session_obj.pending_action.action_id != request.action_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action_id mismatch.")
    if session_obj.pending_action.status != PendingActionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action is already in state: {session_obj.pending_action.status}",
        )

    session_obj.pending_action.status   = PendingActionStatus.REJECTED
    session_obj.pending_action.resolved_at = datetime.now(timezone.utc)
    session_obj.pending_action.resolved_by = current_user["user_id"]
    await store.save(session_obj)

    logger.info(
        "HITL REJECTED action_id=%s session=%s by user=%s",
        request.action_id, session_id, current_user["user_id"],
    )

    directive_id: Optional[str] = None
    if request.correction_directive and request.correction_directive.strip():
        from memory.alignment_store import get_alignment_store, CorrectionDirective
        from telemetry import TamperEvidentAuditLog

        target = (request.agent_target or "GLOBAL").strip()
        directive = CorrectionDirective(
            agent_target   = target,
            directive_text = request.correction_directive.strip(),
            author_id      = current_user["user_id"],
        )
        alignment_store = get_alignment_store()
        if not alignment_store._initialized:
            await alignment_store.initialize()
        await alignment_store.add_directive(directive)
        directive_id = directive.id

        # Cryptographically sign and chain the alignment event so auditors
        # know exactly who changed the AI's behaviour and when.
        import asyncio as _asyncio_local
        _asyncio_local.create_task(
            TamperEvidentAuditLog.record(
                event_type = "ALIGNMENT_DIRECTIVE_ADDED",
                entity_id  = directive.id,
                payload    = {
                    "directive_id":    directive.id,
                    "agent_target":    directive.agent_target,
                    "directive_text":  directive.directive_text,
                    "author_id":       directive.author_id,
                    "timestamp":       directive.timestamp,
                    "rejected_action": request.action_id,
                    "session_id":      session_id,
                },
            )
        )
        logger.info(
            "ALIGNMENT directive id=%s target=%s saved and chained by user=%s",
            directive.id[:8], directive.agent_target, current_user["user_id"],
        )

    return {
        "status":        "rejected",
        "session_id":    session_id,
        "action_id":     request.action_id,
        "resolved_by":   current_user["user_id"],
        "directive_id":  directive_id,
    }


# ============================================
# Alignment Knowledge Base — Admin Endpoints
# ============================================

@app.get(
    "/api/v1/alignment/directives",
    summary="List all alignment directives in the knowledge base",
    tags=["Mission Control"],
)
async def list_alignment_directives(
    current_user: dict = Depends(verify_token),
):
    """
    Return all CorrectionDirectives ever recorded (active and deactivated).
    Only Engineers and Managers may access this endpoint.
    """
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may view alignment directives.",
        )
    from memory.alignment_store import get_alignment_store
    store = get_alignment_store()
    if not store._initialized:
        await store.initialize()
    return {
        "directives": [d.model_dump() for d in store.list_all()],
        "count":      len(store.list_all()),
    }


@app.delete(
    "/api/v1/alignment/directives/{directive_id}",
    summary="Deactivate an alignment directive",
    tags=["Mission Control"],
)
async def deactivate_alignment_directive(
    directive_id: str,
    current_user: dict = Depends(verify_token),
):
    """
    Soft-delete a directive by marking it inactive.  The directive stays in
    the audit chain; only is_active is set to False so it is no longer
    injected into future agent prompts.  Only Managers may deactivate rules.
    """
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Managers may deactivate alignment directives.",
        )
    from memory.alignment_store import get_alignment_store
    from telemetry import TamperEvidentAuditLog
    store = get_alignment_store()
    if not store._initialized:
        await store.initialize()
    directive = await store.deactivate(directive_id)
    if directive is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Directive not found.")

    import asyncio as _asyncio_local
    _asyncio_local.create_task(
        TamperEvidentAuditLog.record(
            event_type = "ALIGNMENT_DIRECTIVE_DEACTIVATED",
            entity_id  = directive.id,
            payload    = {
                "directive_id":   directive.id,
                "agent_target":   directive.agent_target,
                "directive_text": directive.directive_text,
                "deactivated_by": current_user["user_id"],
            },
        )
    )
    return {"status": "deactivated", "directive_id": directive_id}


@app.get(
    "/api/v1/alignment/directives/active/{agent_name}",
    summary="Preview directives currently injected for a specific agent",
    tags=["Mission Control"],
)
async def get_active_directives_for_agent(
    agent_name: str,
    current_user: dict = Depends(verify_token),
):
    """
    Return only the active directives that will be injected into *agent_name*'s
    next system prompt.  Useful for debugging alignment behaviour.
    """
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may preview agent directives.",
        )
    from memory.alignment_store import get_alignment_store
    store = get_alignment_store()
    if not store._initialized:
        await store.initialize()
    active = store.list_active(agent_name)
    return {
        "agent_name": agent_name,
        "directives": [d.model_dump() for d in active],
        "count":      len(active),
    }


@app.get(
    "/api/v1/alignment/cache/status",
    summary="Inspect system-prompt cache version and hit/miss statistics",
    tags=["Mission Control"],
)
async def get_alignment_cache_status(
    current_user: dict = Depends(verify_token),
):
    """
    Return the current LRU cache version and hit/miss info for build_system_prompt.
    Bump signals that a new directive was added and the cache was invalidated.
    """
    from memory.alignment_store import get_cache_version, _cached_build
    info = _cached_build.cache_info()
    return {
        "cache_version": get_cache_version(),
        "lru_hits":      info.hits,
        "lru_misses":    info.misses,
        "lru_maxsize":   info.maxsize,
        "lru_currsize":  info.currsize,
    }


@app.get(
    "/api/v1/agent/telemetry/metrics",
    summary="Aggregated telemetry analytics from the span ring buffer",
    tags=["Mission Control"],
)
async def get_telemetry_metrics(
    limit: int = 500,
    current_user: dict = Depends(verify_token),
):
    """
    Compute and return aggregated runtime analytics from the in-memory span
    ring buffer.  Powers the System Health dashboard panel.

    Metrics returned:
      - ``tool_execution``: success / failure counts and rate.
      - ``token_cost_by_agent``: cumulative USD cost per agent type.
      - ``reflection_loops``: average and total reflection spans.
      - ``span_type_distribution``: counts per span_type tag.
      - ``recent_traces``: lightweight metadata for the 20 most-recent traces.
      - ``total_spans_buffered``: total span count currently in the buffer.
    """
    from telemetry import get_buffered_spans

    spans = get_buffered_spans(limit=limit)

    # ── Tool execution success / failure ────────────────────────────────────
    tool_execs = [s for s in spans if s.span_type == "tool_exec"]
    success_count = sum(1 for s in tool_execs if s.tool_success is True)
    failure_count = sum(1 for s in tool_execs if s.tool_success is False)
    total_tools   = success_count + failure_count

    # ── Token cost grouped by agent ─────────────────────────────────────────
    cost_by_agent: dict = _defaultdict(float)
    tokens_by_agent: dict = _defaultdict(int)
    for s in spans:
        if s.token_usage:
            cost_by_agent[s.agent_name]   += s.token_usage.get("estimated_cost_usd", 0.0)
            tokens_by_agent[s.agent_name] += s.token_usage.get("total_tokens", 0)

    # ── Reflection loops per trace ───────────────────────────────────────────
    reflection_by_trace: dict = _defaultdict(int)
    for s in spans:
        if s.span_type == "reflection":
            reflection_by_trace[s.trace_id] += 1

    avg_reflections = (
        sum(reflection_by_trace.values()) / len(reflection_by_trace)
        if reflection_by_trace else 0.0
    )

    # ── Span type distribution ───────────────────────────────────────────────
    span_type_counts: dict = _defaultdict(int)
    for s in spans:
        span_type_counts[s.span_type] += 1

    # ── Duration percentiles ─────────────────────────────────────────────────
    durations = sorted(s.duration_ms for s in spans)
    p50 = durations[len(durations) // 2]         if durations else 0.0
    p95 = durations[int(len(durations) * 0.95)]  if durations else 0.0
    p99 = durations[int(len(durations) * 0.99)]  if durations else 0.0

    # ── Recent 20 unique traces ──────────────────────────────────────────────
    seen_traces: dict = {}
    for s in reversed(spans):
        if s.trace_id not in seen_traces:
            seen_traces[s.trace_id] = {
                "trace_id":   s.trace_id,
                "agent_name": s.agent_name,
                "started_at": s.started_at,
                "duration_ms": s.duration_ms,
                "has_error":  bool(s.error),
            }
        if len(seen_traces) >= 20:
            break

    return {
        "tool_execution": {
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate":  round(success_count / max(1, total_tools), 4),
        },
        "token_cost_by_agent":  {k: round(v, 8) for k, v in cost_by_agent.items()},
        "tokens_by_agent":      dict(tokens_by_agent),
        "reflection_loops": {
            "avg_per_trace":         round(avg_reflections, 2),
            "total_reflection_spans": span_type_counts.get("reflection", 0),
            "traces_with_reflection": len(reflection_by_trace),
        },
        "span_type_distribution": dict(span_type_counts),
        "latency_percentiles_ms": {"p50": p50, "p95": p95, "p99": p99},
        "recent_traces":          list(seen_traces.values()),
        "total_spans_buffered":   len(spans),
    }


@app.get(
    "/api/v1/agent/session/{session_id}/replay",
    summary="Fetch full reasoning trace for a past session (replay)",
    tags=["Mission Control"],
)
async def replay_session(
    session_id:   str,
    current_user: dict = Depends(verify_token),
):
    """
    Return the complete span trace and session metadata for ``session_id``.

    The Thought Stream component uses this to re-visualise a past agent
    reasoning run without reconnecting to a live SSE stream.

    Spans are sourced from the in-memory ring buffer; they are available
    for as long as the buffer has not been overwritten.  For permanent
    storage configure ``MVA_TELEMETRY_LOG`` to persist spans to JSONL.
    """
    from telemetry import get_trace_spans
    from memory_store import get_memory_store

    spans   = get_trace_spans(session_id)
    store   = get_memory_store()
    session_obj = await store.load(session_id)

    return {
        "session_id":  session_id,
        "span_count":  len(spans),
        "spans":       [s.to_dict() for s in spans],
        "session":     session_obj.model_dump(mode="json") if session_obj else None,
    }


@app.get(
    "/api/v1/agent/sessions",
    summary="List recent agent sessions (for replay picker)",
    tags=["Mission Control"],
)
async def list_agent_sessions(
    limit: int = 50,
    current_user: dict = Depends(verify_token),
):
    """
    Return lightweight metadata for the most-recent ``limit`` sessions.

    Used by the dashboard's Session Replay panel to populate the session
    picker dropdown.
    """
    from memory_store import get_memory_store
    store = get_memory_store()
    sessions = await store.list_sessions(limit=limit)
    return {"sessions": sessions, "count": len(sessions)}


# ============================================
# Health Check
# ============================================

@app.get("/api/v1/health")
async def health_check():
    """API health check"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# ============================================
# Database Persistence Endpoints
# ============================================

@app.post("/api/v1/db/save")
async def save_database(current_user: dict = Depends(verify_token)):
    """Manually save database to JSON file (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can save database")
    
    success = save_db_to_json()
    if success:
        return {"status": "success", "message": "Database saved", "path": str(DB_JSON_PATH)}
    else:
        raise HTTPException(status_code=500, detail="Failed to save database")


@app.post("/api/v1/db/load")
async def load_database(current_user: dict = Depends(verify_token)):
    """Manually load database from JSON file (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can load database")
    
    success = load_db_from_json()
    if success:
        return {"status": "success", "message": "Database loaded", "path": str(DB_JSON_PATH)}
    else:
        return {"status": "warning", "message": "No persistent file found or failed to load"}


@app.get("/api/v1/db/status")
async def database_status(current_user: dict = Depends(verify_token)):
    """Get database persistence status"""
    file_exists = DB_JSON_PATH.exists()
    file_size = DB_JSON_PATH.stat().st_size if file_exists else 0
    file_modified = datetime.fromtimestamp(DB_JSON_PATH.stat().st_mtime).isoformat() if file_exists else None
    
    return {
        "auto_save_enabled": AUTO_SAVE_ENABLED,
        "persistent_file": str(DB_JSON_PATH),
        "file_exists": file_exists,
        "file_size_bytes": file_size,
        "last_modified": file_modified,
        "collections": {key: len(db.get(key, [])) for key in PERSISTENT_KEYS if key in db}
    }


@app.delete("/api/v1/db/reset")
async def reset_database(current_user: dict = Depends(verify_token)):
    """Reset database to defaults and delete persistent file (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can reset database")
    
    reset_db_to_defaults()
    return {
        "status": "success", 
        "message": "Persistent file deleted. Restart server to reset to defaults."
    }


@app.get("/api/v1/db/export")
async def export_database(current_user: dict = Depends(verify_token)):
    """Export full database as JSON (Manager only)"""
    if current_user["role"] != UserRole.MANAGER:
        raise HTTPException(status_code=403, detail="Only managers can export database")
    
    export_data = {}
    for key in PERSISTENT_KEYS:
        if key in db:
            export_data[key] = db[key]
    
    export_data["_meta"] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": current_user["name"],
        "version": "1.0"
    }
    
    return export_data


# ============================================
# IoT Watchdog — Background Task Lifecycle
# ============================================

@app.on_event("startup")
async def _startup_iot_watchdog():
    """
    Start the IoT Watchdog background task when the FastAPI application boots.

    The watchdog runs in the same asyncio event loop as the FastAPI server
    (via asyncio.create_task) so it never spawns threads or extra processes.
    """
    from events.iot_watchdog import start_watchdog
    await start_watchdog()


@app.on_event("startup")
async def _startup_alignment_store():
    """
    Pre-warm the AlignmentStore from its JSON persistence file.

    Runs once at boot so the first build_system_prompt() call during a
    Swarm Debate is guaranteed to hit the in-memory dict rather than
    triggering a cold disk read mid-debate.
    """
    from memory.alignment_store import get_alignment_store
    store = get_alignment_store()
    await store.initialize()
    import logging as _log
    _log.getLogger(__name__).info("AlignmentStore pre-warmed at startup.")


@app.on_event("shutdown")
async def _shutdown_iot_watchdog():
    """
    Gracefully cancel the watchdog background task on application shutdown.

    Waits up to 5 seconds for the task to exit cleanly before returning, so
    no CancelledError tracebacks leak into the log on SIGTERM/SIGINT.
    """
    from events.iot_watchdog import stop_watchdog
    await stop_watchdog()


# ============================================
# Watchdog API — Status, Proposals, and Emergency SSE Stream
# ============================================

# In-memory proposal store: proposal_id → dict
# Keyed here so the watchdog stream and HTTP endpoints share the same store.
_emergency_proposals: dict = {}


@app.get(
    "/api/v1/watchdog/status",
    summary="IoT Watchdog runtime status",
    tags=["Watchdog"],
)
async def watchdog_status(current_user: dict = Depends(verify_token)):
    """Return live status of the IoT Watchdog background task."""
    from events.iot_watchdog import get_watchdog_status
    return get_watchdog_status()


@app.get(
    "/api/v1/watchdog/proposals",
    summary="List all unresolved Emergency Proposals",
    tags=["Watchdog"],
)
async def list_emergency_proposals(current_user: dict = Depends(verify_token)):
    """Return all PENDING_APPROVAL emergency proposals produced by the Watchdog."""
    items = [
        p for p in _emergency_proposals.values()
        if p.get("status") == "PENDING_APPROVAL"
    ]
    return {"proposals": items, "count": len(items)}


class _ProposalDecision(BaseModel):
    proposal_id: str = Field(..., min_length=32, max_length=64)


@app.post(
    "/api/v1/watchdog/proposals/{proposal_id}/approve",
    summary="Approve an Emergency Proposal (HITL one-click action)",
    tags=["Watchdog"],
)
async def approve_emergency_proposal(
    proposal_id:  str,
    current_user: dict = Depends(verify_token),
):
    """
    Mark an Emergency Proposal as APPROVED.

    Only Engineers and Managers may approve emergency proposals.
    A structured audit log entry is written on approval.
    """
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may approve emergency proposals.",
        )
    if proposal_id not in _emergency_proposals:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    _emergency_proposals[proposal_id]["status"]      = "APPROVED"
    _emergency_proposals[proposal_id]["resolved_by"] = current_user["user_id"]
    _emergency_proposals[proposal_id]["resolved_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "EMERGENCY_PROPOSAL APPROVED proposal_id=%s by user=%s",
        proposal_id[:8], current_user["user_id"],
    )
    return {
        "status":      "approved",
        "proposal_id": proposal_id,
        "resolved_by": current_user["user_id"],
    }


@app.post(
    "/api/v1/watchdog/proposals/{proposal_id}/reject",
    summary="Reject an Emergency Proposal",
    tags=["Watchdog"],
)
async def reject_emergency_proposal(
    proposal_id:  str,
    current_user: dict = Depends(verify_token),
):
    """Mark an Emergency Proposal as REJECTED."""
    if current_user["role"] not in (UserRole.MANAGER, UserRole.ENGINEER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Engineers and Managers may reject emergency proposals.",
        )
    if proposal_id not in _emergency_proposals:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    _emergency_proposals[proposal_id]["status"]      = "REJECTED"
    _emergency_proposals[proposal_id]["resolved_by"] = current_user["user_id"]
    _emergency_proposals[proposal_id]["resolved_at"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "EMERGENCY_PROPOSAL REJECTED proposal_id=%s by user=%s",
        proposal_id[:8], current_user["user_id"],
    )
    return {
        "status":      "rejected",
        "proposal_id": proposal_id,
        "resolved_by": current_user["user_id"],
    }


@app.get(
    "/api/v1/watchdog/stream",
    summary="SSE: real-time Emergency Proposal push stream",
    tags=["Watchdog"],
)
async def stream_emergency_proposals(token: Optional[str] = None):
    """
    Server-Sent Events stream that pushes ``EMERGENCY_PROPOSAL`` events to all
    connected Mission Control clients whenever the IoT Watchdog detects an
    anomaly and the Swarm reaches consensus.

    Unlike the per-session ``/agent/stream/{session_id}`` endpoint, this
    stream is global — every connected dashboard client receives every
    emergency proposal simultaneously.

    Authentication via ``?token=`` query param (same as the span stream).

    Event format::

        event: EMERGENCY_PROPOSAL
        data: <JSON-encoded EmergencyProposal>

    Keep-alive ``": ping"`` comments are sent every 30 s to prevent proxy
    / load-balancer timeouts.
    """
    _sse_verify_token(token)

    from telemetry import subscribe_to_emergency, unsubscribe_from_emergency

    async def _event_gen():
        queue = await subscribe_to_emergency()
        try:
            while True:
                try:
                    proposal = await _asyncio.wait_for(queue.get(), timeout=30.0)
                    # Persist to in-memory store so the proposals list endpoint
                    # reflects the latest state.
                    payload = proposal.to_dict()
                    _emergency_proposals[proposal.proposal_id] = payload
                    yield (
                        f"event: EMERGENCY_PROPOSAL\n"
                        f"data: {proposal.to_json()}\n\n"
                    )
                except _asyncio.TimeoutError:
                    yield ": ping\n\n"
        except GeneratorExit:
            pass
        finally:
            await unsubscribe_from_emergency(queue)

    return _StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================
# Cryptographic Audit Chain Endpoint
# ============================================

@app.get("/api/v1/audit/verify_chain")
async def verify_audit_chain(current_user: dict = Depends(verify_token)):
    """
    Verify the integrity of the tamper-evident audit chain.

    Re-reads every block from the JSONL file on disk, recomputes SHA-256 hash
    linkages, and re-verifies Ed25519 signatures.

    Returns
    -------
    JSON
        ``{"status": "SECURE"|"COMPROMISED",
           "total_blocks": int,
           "tampered_blocks": [...],
           "verified_signatures": int}``
    """
    from telemetry import TamperEvidentAuditLog
    result = await TamperEvidentAuditLog.verify_chain()
    return result


# ============================================
# Run Server
# ============================================

# Serve static files (HTML, CSS, JS) from parent directory
STATIC_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/ddm_p1_full.html")
async def serve_main_page():
    """Serve the main HTML page"""
    html_path = STATIC_DIR / "ddm_p1_full.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="HTML file not found")

@app.get("/")
async def root():
    """Redirect root to main page"""
    return FileResponse(STATIC_DIR / "ddm_p1_full.html", media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
