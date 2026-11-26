"""RM Simulator Unified Desktop Launcher (Tkinter).

Provides a single GUI entry point to:
- Pick any vehicle package (built-in or external vehicle.json / vehicle.canonical.json)
- Pick any map package and spawn point
- Choose mode: Pure Simulator + ROS 2 Bridge (external robot plug-in), Manual Drive,
  Training Range, Built-in Navigation Baseline (32.2s Curvature-Profiled MPC 0-overlap μ=1.05), or War Thunder Flanders
- Optionally launch the decoupled external sentry_ws connector against a live simulator
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

REPO_ROOT = Path(__file__).resolve().parents[2]
VEHICLES_DIR = REPO_ROOT / "assets" / "vehicles"
MAPS_DIR = REPO_ROOT / "assets" / "maps"


def load_json_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def discover_vehicles() -> list[dict]:
    items: list[dict] = []
    if not VEHICLES_DIR.is_dir():
        return items
    for entry in sorted(VEHICLES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        mesh_rig = entry / "ue_mesh_rig.json"
        canonical = entry / "vehicle.canonical.json"
        manifest = entry / "vehicle.json"
        if mesh_rig.is_file():
            data = load_json_safe(mesh_rig) or {}
            items.append(
                {
                    "id": data.get("id", entry.name),
                    "display_name": data.get("display_name", f"{entry.name} (MeshRig Swerve)"),
                    "path": str(entry),
                    "kind": "mesh_rig",
                    "control_type": "swerve_4w",
                    "summary": "AIM 4舵轮接触模型 (15部件 / 2000 N/m 悬挂 / 5 m/s)",
                }
            )
        elif canonical.is_file() or manifest.is_file():
            src = canonical if canonical.is_file() else manifest
            data = load_json_safe(src) or {}
            ctrl = data.get("control") or {}
            lim = ctrl.get("limits") or {}
            sensors = [s.get("type", "?") for s in data.get("sensors", [])]
            items.append(
                {
                    "id": data.get("id", entry.name),
                    "display_name": data.get("display_name", entry.name),
                    "path": str(entry),
                    "kind": "canonical",
                    "control_type": ctrl.get("type", "unknown"),
                    "summary": (
                        f"通用 URDF 车辆 | 驱动: {ctrl.get('type', '?')} | "
                        f"限速: {lim.get('linear_mps', '?')} m/s, {lim.get('angular_rps', '?')} rad/s | "
                        f"传感器: {', '.join(sensors) if sensors else '无'}"
                    ),
                }
            )
    return items


def discover_maps() -> list[dict]:
    items: list[dict] = []
    if not MAPS_DIR.is_dir():
        return items
    for entry in sorted(MAPS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        mf = entry / "map.json"
        if not mf.is_file():
            continue
        data = load_json_safe(mf) or {}
        entries = [s for s in data.get("spawns", []) if isinstance(s, dict)]
        spawns = [s.get("id", "default") for s in entries]
        if not spawns:
            spawns = ["default"]
        items.append(
            {
                "id": data.get("id", entry.name),
                "display_name": data.get("display_name", entry.name),
                "path": str(mf),
                "kind": data.get("kind", "unknown"),
                "spawns": spawns,
                "team_spawns": {s["team"]: s["id"] for s in entries if "team" in s and "id" in s},
            }
        )
    return items


def match_roster_payload(roster: dict[str, list[dict]], pilot: tuple[str, int],
                         map_entry: dict, auto_sentry: bool) -> dict:
    teams = ("red", "blue")
    if any(len(roster[team]) != 1 for team in teams):
        raise ValueError("红蓝双方各需一辆 AIM 哨兵。")
    if any(roster[team][0]["vehicle_id"] != "aim_sentry" or roster[team][0]["role"] != "S7 哨兵"
           for team in teams):
        raise ValueError("当前比赛支持双方各一辆 AIM S7 哨兵。")
    primary_team, primary_idx = pilot
    if primary_team not in teams or primary_idx != 0:
        raise ValueError("请为比赛选择有效的视角。")
    if not all(map_entry["team_spawns"].get(team) for team in teams):
        raise ValueError("比赛地图必须同时具有红方和蓝方出生点。")
    ros_teams = [team for team in teams if roster[team][0]["control"] == "sentry_ws"]
    if len(ros_teams) > 2:
        raise ValueError("一场比赛最多支持两辆 sentry_ws 哨兵（红蓝各一）。")
    primary = roster[primary_team][primary_idx]
    ros_team = "both" if len(ros_teams) == 2 else (ros_teams[0] if ros_teams else "")
    return {
        "map_id": map_entry["id"],
        "map_path": map_entry["path"],
        "red": roster["red"],
        "blue": roster["blue"],
        "primary_team": primary_team,
        "primary_index": primary_idx,
        "primary_role": primary["role"],
        "primary_control": primary["control"],
        "ros_team": ros_team,
        "ros_teams": ros_teams,
        "auto_sentry_ws": bool(ros_teams and auto_sentry),
    }


class RMSimLauncherApp(tk.Tk):
    def __init__(self, initial_mode: str = "match") -> None:
        super().__init__()
        self.title("RM Simulator — 统一仿真启动器 (RMUC 2027)")
        self.geometry("940x790")
        self.minsize(840, 700)
        self.configure(bg="#14181f")

        self.initial_mode = initial_mode
        self.vehicles = discover_vehicles()
        self.maps = discover_maps()
        self.active_procs: list[subprocess.Popen] = []
        self.match_connector: list[str] | None = None
        self.match_owns_connector = False
        default_veh = "aim_sentry"
        self.match_roster: dict[str, list[dict]] = {
            "red": [
                {"role": "S7 哨兵", "vehicle_id": default_veh, "control": "sentry_ws"},
            ],
            "blue": [
                {"role": "S7 哨兵", "vehicle_id": default_veh, "control": "manual"},
            ],
        }
        # Explicit operator pilot target: (team_key, index_in_team_list)
        self.pilot_target: tuple[str, int] = ("blue", 0)

        self._setup_style()
        self._build_ui()
        self._on_vehicle_changed()
        self._on_map_changed()
        self._refresh_roster_lists()
        self._on_mode_changed()

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        bg = "#14181f"
        card = "#1c222d"
        fg = "#e8edf5"
        muted = "#9aa7b8"
        accent = "#2d7ff9"

        style.configure(".", background=bg, foreground=fg, font=("Segoe UI", 10))
        style.configure("Card.TLabelframe", background=card, foreground=fg, bordercolor="#2a3342", relief="solid")
        style.configure("Card.TLabelframe.Label", background=card, foreground="#7ab4ff", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TFrame", background=card)
        style.configure("Card.TLabel", background=card, foreground=fg)
        style.configure("Muted.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        style.configure("Card.TRadiobutton", background=card, foreground=fg, font=("Segoe UI", 10))
        style.map("Card.TRadiobutton", background=[("active", "#232b38")])
        style.configure("Card.TCheckbutton", background=card, foreground=fg)
        style.map("Card.TCheckbutton", background=[("active", "#232b38")])
        style.configure("Accent.TButton", background=accent, foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(14, 8))
        style.map("Accent.TButton", background=[("active", "#1f6be0")])
        style.configure("Secondary.TButton", background="#283142", foreground=fg, font=("Segoe UI", 10), padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", "#344056")])
        style.configure("Danger.TButton", background="#b83232", foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#9c2626")])

    def _build_ui(self) -> None:
        # Header
        header = tk.Frame(self, bg="#10141a", pady=12, padx=18)
        header.pack(fill="x")
        tk.Label(
            header,
            text="RM Simulator 统一启动器",
            bg="#10141a",
            fg="#ffffff",
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text="UE5 + MuJoCo 4.0.1 + ROS 2 解耦底座",
            bg="#10141a",
            fg="#7ab4ff",
            font=("Segoe UI", 10),
        ).pack(side="right")

        body = tk.Frame(self, bg="#14181f", padx=16, pady=10)
        body.pack(fill="both", expand=True)

        # 1. Mode Selection
        mode_frame = ttk.LabelFrame(body, text=" 1. 运行模式 ", style="Card.TLabelframe", padding=10)
        mode_frame.pack(fill="x", pady=(0, 10))
        self.mode_var = tk.StringVar(value=self.initial_mode)
        modes = [
            ("match", "🏆 RMUC 2027 比赛模式（红蓝 AIM 哨兵 · ROS 2 与手操席）"),
            ("pure_ros", "纯仿真 + 标准 ROS 2 桥（外部机器人/sentry_ws 接入）"),
            ("manual", "手动试驾（UE5 + MuJoCo，无 ROS 2）"),
            ("training", "静态靶射击训练（手动 + 弹道/靶场）"),
            ("nav_baseline", "内置哨兵导航基线（Play-Navigation.cmd · RMUC 32.2s 曲率规划 MPC · 0 碰撞）"),
            ("flanders", "弗兰德斯战雷原图（Play-WarThunder.cmd）"),
        ]
        for idx, (val, label) in enumerate(modes):
            rb = ttk.Radiobutton(
                mode_frame,
                text=label,
                value=val,
                variable=self.mode_var,
                style="Card.TRadiobutton",
                command=self._on_mode_changed,
            )
            rb.grid(row=idx // 2, column=idx % 2, sticky="w", padx=8, pady=3)

        # 2A. Match Mode Two-Team Roster Builder (shown in match mode)
        self.match_frame = ttk.LabelFrame(
            body,
            text=" 2. RMUC 2027 比赛编队 · 红蓝各一辆 AIM 哨兵 ",
            style="Card.TLabelframe",
            padding=10,
        )
        self.match_frame.columnconfigure(0, weight=1)
        self.match_frame.columnconfigure(1, weight=1)

        veh_ids = ["aim_sentry"]
        roles = ["S7 哨兵"]
        ctrl_labels = ["sentry_ws 导航 (ROS 2)", "操作手手操 (键鼠)", "裁判AI目标车"]
        self._ctrl_label_to_key = {
            "sentry_ws 导航 (ROS 2)": "sentry_ws",
            "操作手手操 (键鼠)": "manual",
            "裁判AI目标车": "ai",
        }
        self._ctrl_key_to_label = {v: k for k, v in self._ctrl_label_to_key.items()}

        self.team_listboxes: dict[str, tk.Listbox] = {}
        self.team_role_vars: dict[str, tk.StringVar] = {}
        self.team_veh_vars: dict[str, tk.StringVar] = {}
        self.team_ctrl_vars: dict[str, tk.StringVar] = {}

        for col, (team_key, title_txt, title_col) in enumerate([
            ("red", "红方编队 (RED ALLIANCE)", "#f06a52"),
            ("blue", "蓝方编队 (BLUE ALLIANCE)", "#58b8f0"),
        ]):
            tbox = tk.Frame(self.match_frame, bg="#181e28", bd=1, relief="solid", padx=8, pady=6)
            tbox.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 6, 6 if col == 0 else 0))
            tk.Label(tbox, text=title_txt, bg="#181e28", fg=title_col, font=("Segoe UI", 10, "bold")).pack(anchor="w")

            lb = tk.Listbox(
                tbox,
                height=4,
                bg="#10151d",
                fg="#e8edf5",
                selectbackground="#2d7ff9",
                font=("Consolas", 9),
                relief="flat",
                exportselection=False,
            )
            lb.pack(fill="x", pady=(4, 6))
            lb.bind("<Double-Button-1>", lambda _e, tk_key=team_key: self._set_pilot_vehicle(tk_key))
            self.team_listboxes[team_key] = lb

            add_row = tk.Frame(tbox, bg="#181e28")
            add_row.pack(fill="x")
            r_var = tk.StringVar(value=roles[0])
            v_var = tk.StringVar(value=veh_ids[0])
            c_var = tk.StringVar(value=ctrl_labels[0 if team_key == "red" else 1])
            self.team_role_vars[team_key] = r_var
            self.team_veh_vars[team_key] = v_var
            self.team_ctrl_vars[team_key] = c_var

            ttk.Combobox(add_row, textvariable=r_var, values=roles, width=8, state="readonly").pack(side="left", padx=(0, 4))
            ttk.Combobox(add_row, textvariable=v_var, values=veh_ids, width=11, state="readonly").pack(side="left", padx=(0, 4))
            ttk.Combobox(add_row, textvariable=c_var, values=ctrl_labels, width=21, state="readonly").pack(side="left", padx=(0, 4))

            act_row = tk.Frame(tbox, bg="#181e28")
            act_row.pack(fill="x", pady=(5, 0))
            ttk.Button(
                act_row,
                text="应用车辆配置",
                style="Secondary.TButton",
                command=lambda tk_key=team_key: self._add_match_vehicle(tk_key),
            ).pack(side="left", padx=(0, 6))
            ttk.Button(
                act_row,
                text="★ 设为我的视角",
                style="Secondary.TButton",
                command=lambda tk_key=team_key: self._set_pilot_vehicle(tk_key),
            ).pack(side="left", padx=(0, 6))
            ttk.Button(
                act_row,
                text="－ 移除选中",
                style="Secondary.TButton",
                command=lambda tk_key=team_key: self._remove_match_vehicle(tk_key),
            ).pack(side="left")

        pilot_bar = tk.Frame(self.match_frame, bg="#202836", padx=10, pady=5)
        pilot_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.pilot_summary_var = tk.StringVar(value="")
        tk.Label(
            pilot_bar,
            textvariable=self.pilot_summary_var,
            bg="#202836",
            fg="#f0d2a8",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Label(
            pilot_bar,
            text="双击车辆或点「★ 设为我的视角」选择驾驶席",
            bg="#202836",
            fg="#9aa7b8",
            font=("Segoe UI", 8),
        ).pack(side="right")

        match_opt_row = tk.Frame(self.match_frame, bg="#1c222d")
        match_opt_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.match_preparation_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            match_opt_row,
            text="赛前准备（F6 跳过：按1下10s倒计时 / 按2下直接开赛）",
            variable=self.match_preparation_var,
            style="Card.TCheckbutton",
        ).pack(side="left", padx=(0, 12))
        self.match_auto_sentry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            match_opt_row,
            text="开赛后自动接入 sentry_ws (Point-LIO + Nav2 + 自瞄)",
            variable=self.match_auto_sentry_var,
            style="Card.TCheckbutton",
        ).pack(side="left")
        ttk.Label(
            match_opt_row,
            text="地图锁定: RMUC 2027 全尺寸赛场",
            style="Muted.TLabel",
        ).pack(side="right")

        # 2B. Single Vehicle & Map Selection (shown in non-match modes)
        self.vm_frame = ttk.LabelFrame(body, text=" 2. 车辆包与地图选择（车图解耦） ", style="Card.TLabelframe", padding=10)
        vm_frame = self.vm_frame
        vm_frame.pack(fill="x", pady=(0, 10))
        vm_frame.columnconfigure(1, weight=1)

        ttk.Label(vm_frame, text="车辆包:", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.vehicle_var = tk.StringVar()
        self.vehicle_combo = ttk.Combobox(
            vm_frame,
            textvariable=self.vehicle_var,
            values=[f"{v['id']} — {v['display_name']}" for v in self.vehicles],
            state="readonly",
        )
        if self.vehicles:
            self.vehicle_combo.current(0)
        self.vehicle_combo.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        self.vehicle_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_vehicle_changed())
        ttk.Button(vm_frame, text="浏览外部车辆包...", style="Secondary.TButton", command=self._browse_vehicle).grid(
            row=0, column=2, padx=6, pady=4
        )

        self.vehicle_info_var = tk.StringVar(value="")
        ttk.Label(vm_frame, textvariable=self.vehicle_info_var, style="Muted.TLabel").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 6)
        )

        ttk.Label(vm_frame, text="地图:", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.map_var = tk.StringVar()
        self.map_combo = ttk.Combobox(
            vm_frame,
            textvariable=self.map_var,
            values=[f"{m['id']} ({m['kind']}) — {m['display_name']}" for m in self.maps],
            state="readonly",
        )
        default_map_idx = next((i for i, m in enumerate(self.maps) if m["id"] == "rmuc2027"), 0)
        if self.maps:
            self.map_combo.current(default_map_idx)
        self.map_combo.grid(row=2, column=1, sticky="ew", padx=6, pady=4)
        self.map_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_map_changed())

        spawn_box = ttk.Frame(vm_frame, style="Card.TFrame")
        spawn_box.grid(row=2, column=2, sticky="ew", padx=6, pady=4)
        ttk.Label(spawn_box, text="出生点:", style="Card.TLabel").pack(side="left", padx=(0, 4))
        self.spawn_var = tk.StringVar(value="default")
        self.spawn_combo = ttk.Combobox(spawn_box, textvariable=self.spawn_var, width=14, state="readonly")
        self.spawn_combo.pack(side="left")

        # 3. ROS 2 & Physics Settings
        self.cfg_frame = ttk.LabelFrame(body, text=" 3. 物理后端 / ROS 2 外部接入参数 ", style="Card.TLabelframe", padding=10)
        cfg_frame = self.cfg_frame
        cfg_frame.pack(fill="x", pady=(0, 10))
        for col in (1, 3, 5):
            cfg_frame.columnconfigure(col, weight=1)

        ttk.Label(cfg_frame, text="Robot ID:", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.robot_id_var = tk.StringVar(value="aim_sentry")
        ttk.Entry(cfg_frame, textvariable=self.robot_id_var, width=16).grid(row=0, column=1, sticky="ew", padx=6, pady=4)

        ttk.Label(cfg_frame, text="TCP Port:", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=6, pady=4)
        self.port_var = tk.StringVar(value="9451")
        ttk.Entry(cfg_frame, textvariable=self.port_var, width=10).grid(row=0, column=3, sticky="ew", padx=6, pady=4)

        ttk.Label(cfg_frame, text="ROS_DOMAIN_ID:", style="Card.TLabel").grid(row=0, column=4, sticky="w", padx=6, pady=4)
        self.domain_var = tk.StringVar(value="71")
        ttk.Entry(cfg_frame, textvariable=self.domain_var, width=10).grid(row=0, column=5, sticky="ew", padx=6, pady=4)

        ttk.Label(cfg_frame, text="物理后端:", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.physics_var = tk.StringVar(value="mujoco")
        ttk.Combobox(cfg_frame, textvariable=self.physics_var, values=["mujoco", "chaos"], state="readonly", width=12).grid(
            row=1, column=1, sticky="ew", padx=6, pady=4
        )

        ttk.Label(cfg_frame, text="Camera Hz:", style="Card.TLabel").grid(row=1, column=2, sticky="w", padx=6, pady=4)
        self.camera_hz_var = tk.StringVar(value="0")
        ttk.Combobox(cfg_frame, textvariable=self.camera_hz_var, values=["0", "10", "15", "30"], width=10).grid(
            row=1, column=3, sticky="ew", padx=6, pady=4
        )

        ttk.Label(cfg_frame, text="渲染比例:", style="Card.TLabel").grid(row=1, column=4, sticky="w", padx=6, pady=4)
        self.scale_var = tk.StringVar(value="50")
        ttk.Combobox(cfg_frame, textvariable=self.scale_var, values=["50", "75", "100"], state="readonly", width=10).grid(
            row=1, column=5, sticky="ew", padx=6, pady=4
        )

        self.imu_var = tk.BooleanVar(value=True)
        self.lidar_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_frame, text="启用 ROS IMU", variable=self.imu_var, style="Card.TCheckbutton").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=4
        )
        ttk.Checkbutton(cfg_frame, text="启用 ROS LiDAR", variable=self.lidar_var, style="Card.TCheckbutton").grid(
            row=2, column=2, columnspan=2, sticky="w", padx=6, pady=4
        )

        ttk.Label(cfg_frame, text="外部 sentry YAML:", style="Card.TLabel").grid(row=3, column=0, sticky="w", padx=6, pady=4)
        self.nav_yaml_var = tk.StringVar(
            value=str(REPO_ROOT / "ros2_ws/src/pb2025_nav_bringup/config/simulation/nav2_params.yaml")
        )
        ttk.Entry(cfg_frame, textvariable=self.nav_yaml_var).grid(row=3, column=1, columnspan=4, sticky="ew", padx=6, pady=4)
        ttk.Button(cfg_frame, text="选择 YAML...", style="Secondary.TButton", command=self._browse_yaml).grid(
            row=3, column=5, padx=6, pady=4
        )

        # 4. Action Buttons
        btn_bar = tk.Frame(body, bg="#14181f", pady=6)
        btn_bar.pack(fill="x")
        self.launch_btn_var = tk.StringVar(value="🏆 开启比赛")
        self.launch_btn = ttk.Button(
            btn_bar,
            textvariable=self.launch_btn_var,
            style="Accent.TButton",
            command=self._launch_simulator,
        )
        self.launch_btn.pack(side="left", padx=(0, 10))
        ttk.Button(
            btn_bar,
            text="🔗 单独对接外部 sentry_ws",
            style="Secondary.TButton",
            command=self._launch_external_sentry,
        ).pack(side="left", padx=(0, 10))
        ttk.Button(btn_bar, text="■ 停止全部会话", style="Danger.TButton", command=self._stop_all).pack(side="right")

        # 5. Live Log Console
        log_frame = ttk.LabelFrame(body, text=" 运行状态与 ROS 2 契约日志 ", style="Card.TLabelframe", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(6, 0))
        self.log_text = tk.Text(
            log_frame,
            height=11,
            bg="#0e1218",
            fg="#c8d6e5",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            relief="flat",
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)
        self._log("就绪。选择车辆、地图和模式后点击「启动仿真」。")

    def _log(self, msg: str) -> None:
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    def _selected_vehicle(self) -> dict | None:
        idx = self.vehicle_combo.current()
        if 0 <= idx < len(self.vehicles):
            return self.vehicles[idx]
        return None

    def _selected_map(self) -> dict | None:
        idx = self.map_combo.current()
        if 0 <= idx < len(self.maps):
            return self.maps[idx]
        return None

    def _on_vehicle_changed(self) -> None:
        v = self._selected_vehicle()
        if not v:
            return
        self.vehicle_info_var.set(v["summary"])
        self.robot_id_var.set(v["id"])

    def _on_map_changed(self) -> None:
        m = self._selected_map()
        if not m:
            return
        self.spawn_combo["values"] = m["spawns"]
        self.spawn_var.set(m["spawns"][0])
        if m["kind"] == "rm_scene_map":
            self.physics_var.set("chaos")
        else:
            self.physics_var.set("mujoco")

    def _ensure_valid_pilot(self) -> tuple[str, int] | None:
        pt_team, pt_idx = self.pilot_target
        if pt_team in self.match_roster and 0 <= pt_idx < len(self.match_roster[pt_team]):
            return (pt_team, pt_idx)
        for team_key in ("blue", "red"):
            for i, item in enumerate(self.match_roster[team_key]):
                if item["control"] == "manual":
                    self.pilot_target = (team_key, i)
                    return self.pilot_target
        for team_key in ("red", "blue"):
            if self.match_roster[team_key]:
                self.pilot_target = (team_key, 0)
                return self.pilot_target
        return None

    def _refresh_roster_lists(self) -> None:
        valid_pilot = self._ensure_valid_pilot()
        for team_key, lb in self.team_listboxes.items():
            lb.delete(0, "end")
            for idx, item in enumerate(self.match_roster[team_key]):
                ctrl_txt = self._ctrl_key_to_label.get(item["control"], item["control"])
                star = "★ [我的视角] " if valid_pilot == (team_key, idx) else "   "
                lb.insert("end", f"{star}{idx + 1}. [{item['role']}] {item['vehicle_id']} | {ctrl_txt}")
        if valid_pilot:
            pt_team, pt_idx = valid_pilot
            p_item = self.match_roster[pt_team][pt_idx]
            team_cn = "红方" if pt_team == "red" else "蓝方"
            mode_cn = "键鼠手操" if p_item["control"] == "manual" else ("随车观战 sentry_ws 导航" if p_item["control"] == "sentry_ws" else "观战 AI")
            self.pilot_summary_var.set(f"★ 当前驾驶席: {team_cn} · {p_item['role']} ({p_item['vehicle_id']}) — {mode_cn}")
        else:
            self.pilot_summary_var.set("★ 当前驾驶席: 未选择（请先添加至少 1 辆车）")

    def _set_pilot_vehicle(self, team_key: str) -> None:
        lb = self.team_listboxes[team_key]
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("提示", f"请先在{'红方' if team_key == 'red' else '蓝方'}列表中点击选中一辆车。")
            return
        idx = sel[0]
        if 0 <= idx < len(self.match_roster[team_key]):
            self.pilot_target = (team_key, idx)
            item = self.match_roster[team_key][idx]
            self._refresh_roster_lists()
            team_cn = "红方" if team_key == "red" else "蓝方"
            self._log(f"[驾驶席] 已选择: {team_cn} {item['role']} ({item['vehicle_id']}, {item['control']})")

    def _add_match_vehicle(self, team_key: str) -> None:
        role = self.team_role_vars[team_key].get()
        veh_id = self.team_veh_vars[team_key].get()
        ctrl = self._ctrl_label_to_key.get(self.team_ctrl_vars[team_key].get(), "manual")
        self.match_roster[team_key] = [{"role": role, "vehicle_id": veh_id, "control": ctrl}]
        self._refresh_roster_lists()
        self._log(f"[比赛编队] {'红方' if team_key == 'red' else '蓝方'}: {role} ({veh_id}, {ctrl})")

    def _remove_match_vehicle(self, team_key: str) -> None:
        lb = self.team_listboxes[team_key]
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.match_roster[team_key]):
            removed = self.match_roster[team_key].pop(idx)
            self._refresh_roster_lists()
            self._log(f"[比赛编队] 已从{'红方' if team_key == 'red' else '蓝方'}移除: {removed['role']} ({removed['vehicle_id']})")

    def _on_mode_changed(self) -> None:
        mode = self.mode_var.get()
        if mode == "match":
            self.vm_frame.pack_forget()
            self.match_frame.pack(fill="x", pady=(0, 10), before=self.cfg_frame)
            self.launch_btn_var.set("🏆 开启比赛 (START MATCH)")
            return

        self.match_frame.pack_forget()
        if not self.vm_frame.winfo_ismapped():
            self.vm_frame.pack(fill="x", pady=(0, 10), before=self.cfg_frame)
        self.launch_btn_var.set("▶ 启动仿真")
        custom = mode in ("pure_ros", "manual", "training")
        state = "readonly" if custom else "disabled"
        self.vehicle_combo.configure(state=state)
        self.map_combo.configure(state=state)
        self.spawn_combo.configure(state=state)

    def _browse_vehicle(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择外部车辆包 (vehicle.json / vehicle.canonical.json / ue_mesh_rig.json)",
            filetypes=[("Vehicle JSON", "*.json"), ("All Files", "*.*")],
        )
        if not file_path:
            return
        p = Path(file_path)
        data = load_json_safe(p)
        if not data or not data.get("id"):
            messagebox.showerror("无效车辆包", "所选 JSON 缺少 id 字段，请选择合法的 vehicle.json 或 vehicle.canonical.json。")
            return
        ctrl = data.get("control") or {}
        entry = {
            "id": data["id"],
            "display_name": data.get("display_name", data["id"]),
            "path": str(p),
            "kind": "mesh_rig" if p.name == "ue_mesh_rig.json" else "canonical",
            "control_type": ctrl.get("type", "external"),
            "summary": f"外部车辆包: {p} | 驱动: {ctrl.get('type', 'custom')}",
        }
        self.vehicles.append(entry)
        self.vehicle_combo["values"] = [f"{v['id']} — {v['display_name']}" for v in self.vehicles]
        self.vehicle_combo.current(len(self.vehicles) - 1)
        self._on_vehicle_changed()
        self._log(f"已加载外部车辆包: {entry['id']} ({p})")

    def _browse_yaml(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择外部 sentry_ws nav2_params.yaml",
            filetypes=[("YAML", "*.yaml;*.yml"), ("All Files", "*.*")],
        )
        if file_path:
            self.nav_yaml_var.set(file_path)

    def _run_background(self, cmd: list[str], title: str) -> None:
        self._log(f"\n[{title}] 执行: {' '.join(cmd)}")

        def worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(REPO_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self.active_procs.append(proc)
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.after(0, self._log, line.rstrip())
                rc = proc.wait()
                self.after(0, self._log, f"[{title}] 已退出 (code={rc})")
            except Exception as exc:
                self.after(0, self._log, f"[{title}] 启动失败: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _launch_match(self) -> None:
        rmuc_map = next((m for m in self.maps if m["id"] == "rmuc2027"), None)
        vehicle = next((v for v in self.vehicles if v["id"] == "aim_sentry"), None)
        if not rmuc_map or not vehicle:
            messagebox.showerror("缺少比赛资源", "比赛需要 RMUC 2027 地图包和 AIM 哨兵车辆包。")
            return

        pilot = self._ensure_valid_pilot()
        try:
            if pilot is None:
                raise ValueError("红蓝双方各需一辆 AIM 哨兵。")
            roster = match_roster_payload(self.match_roster, pilot, rmuc_map, self.match_auto_sentry_var.get())
            team = roster["primary_team"]
            ros_team = roster["ros_team"]
            port = self.port_var.get().strip() or "9451"
            domain = self.domain_var.get().strip() or "71"
            robot_id = self.robot_id_var.get().strip() or vehicle["id"]
            camera_hz = float(self.camera_hz_var.get().strip() or "15") if ros_team else 0
            max_port = 65534 if ros_team == "both" else 65535
            max_domain = 231 if ros_team == "both" else 232
            if ros_team and not (1024 <= int(port) <= max_port and 0 <= int(domain) <= max_domain
                                 and (camera_hz == 0 or 1 <= camera_hz <= 60)):
                raise ValueError("请检查 ROS 端口（1024–65534）、域（0–231）和相机频率（1–60 Hz，0 使用 15 Hz）。")
        except ValueError as exc:
            messagebox.showwarning("比赛配置", str(exc))
            return

        match_dir = REPO_ROOT / "artifacts" / "unreal" / "match" / uuid.uuid4().hex
        try:
            match_dir.mkdir(parents=True)
            roster_path = match_dir / "match_roster.json"
            roster_path.write_text(json.dumps(roster, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("无法保存比赛编队", str(exc))
            return

        cmd = [
            "powershell.exe", "-NoProfile", "-File",
            str(REPO_ROOT / "tools/unreal/Start-Simulator.ps1"),
            "-Vehicle", vehicle["path"],
            "-Map", rmuc_map["path"],
            "-SpawnId", rmuc_map["team_spawns"][team],
            "-MatchRoster", str(roster_path),
            "-PhysicsBackend", "mujoco",
            "-RenderScale", self.scale_var.get(),
        ]
        self.match_connector = None
        self.match_owns_connector = roster["auto_sentry_ws"]
        if self.match_preparation_var.get():
            cmd.append("-MatchPreparation")
        if ros_team:
            nav_source = self.nav_yaml_var.get().strip()
            cmd += [
                "-RosVehicleTeam", ros_team,
                "-RosBridge", "-RosControl", "-RosAimControl", "-RosImu", "-RosLidar",
                "-RobotId", robot_id,
                "-RosPort", port,
                "-Domain", domain,
                "-CameraHz", str(int(camera_hz) if camera_hz and float(camera_hz).is_integer() else (camera_hz or 15)),
            ]
            if ros_team != "both":
                self.match_connector = self._external_sentry_command(
                    robot_id, domain, rmuc_map["path"], rmuc_map["team_spawns"][ros_team], nav_source,
                )
            if self.match_owns_connector:
                cmd.append("-AutoSentry")
                if nav_source:
                    cmd += ["-NavigationSource", nav_source]

        self._log("\n================ RMUC 2027 比赛开启 ================")
        self._log(f"红方 {roster['red'][0]['control']} · 蓝方 {roster['blue'][0]['control']} · 本机视角 {team}")
        self._log(f"编队清单已写入: {roster_path}")
        if ros_team == "both":
            self._log(
                f"双全自动 ROS 2 哨兵已启用：红方 port={port} domain={domain}，"
                f"蓝方 port={int(port)+1} domain={int(domain)+1}（轻量化分帧自瞄相机）。"
            )
        elif ros_team:
            self._log(f"ROS 2 参赛车辆为 {ros_team} ({robot_id})，定位从 {rmuc_map['team_spawns'][ros_team]} 出生点初始化。")
        self._run_background(cmd, "RMUC 2027 比赛仿真器")

    def _launch_simulator(self) -> None:
        mode = self.mode_var.get()
        if mode == "match":
            self._launch_match()
            return
        if mode == "nav_baseline":
            self._run_background(["cmd", "/c", str(REPO_ROOT / "Play-Navigation.cmd")], "内置导航基线 32.2s 曲率规划 MPC")
            return
        if mode == "flanders":
            self._run_background(["cmd", "/c", str(REPO_ROOT / "Play-WarThunder.cmd")], "弗兰德斯原图")
            return

        v = self._selected_vehicle()
        m = self._selected_map()
        if not v or not m:
            messagebox.showwarning("缺少选择", "请先选择车辆包和地图。")
            return

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(REPO_ROOT / "tools" / "unreal" / "Start-Simulator.ps1"),
            "-Vehicle",
            v["path"],
            "-Map",
            m["path"],
            "-SpawnId",
            self.spawn_var.get(),
            "-RobotId",
            self.robot_id_var.get().strip() or v["id"],
            "-PhysicsBackend",
            self.physics_var.get(),
            "-RenderScale",
            self.scale_var.get(),
        ]
        if mode == "training":
            cmd.append("-TrainingMode")
        elif mode == "pure_ros":
            cmd += [
                "-RosBridge",
                "-RosControl",
                "-RosPort",
                self.port_var.get().strip() or "9451",
                "-Domain",
                self.domain_var.get().strip() or "71",
                "-CameraHz",
                self.camera_hz_var.get().strip() or "0",
            ]
            if self.imu_var.get():
                cmd.append("-RosImu")
            if self.lidar_var.get():
                cmd.append("-RosLidar")

        self._run_background(cmd, f"仿真器 ({v['id']} @ {m['id']})")

    @staticmethod
    def _external_sentry_command(robot_id: str, domain: str, map_path: str,
                                 spawn_id: str, nav_source: str) -> list[str]:
        cmd = [
            "powershell.exe", "-NoProfile", "-File",
            str(REPO_ROOT / "tools/environment/Start-ExternalSentry.ps1"),
            "-RobotId", robot_id,
            "-Domain", domain,
            "-MapFile", map_path,
            "-SpawnId", spawn_id,
        ]
        if nav_source:
            cmd += ["-NavigationSource", nav_source]
        return cmd

    def _launch_external_sentry(self) -> None:
        if self.mode_var.get() == "match":
            if self.match_owns_connector:
                messagebox.showinfo("比赛自动接入", "当前比赛由仿真启动器管理 sentry_ws。")
            elif self.match_connector:
                self._run_background(self.match_connector, "比赛 sentry_ws 连接器")
            else:
                messagebox.showwarning("缺少比赛会话", "请先启动配置了一辆 sentry_ws 哨兵的比赛。")
            return
        selected_map = self._selected_map()
        if not selected_map:
            messagebox.showwarning("缺少选择", "请先选择仿真器正在使用的地图。")
            return
        cmd = self._external_sentry_command(
            self.robot_id_var.get().strip() or "aim_sentry",
            self.domain_var.get().strip() or "71",
            selected_map["path"],
            self.spawn_var.get(),
            self.nav_yaml_var.get().strip(),
        )
        self._run_background(cmd, "外部 sentry_ws 连接器")

    def _stop_all(self) -> None:
        self._log("\n正在停止所有活动会话（含 UE5 / ROS 2 / RViz 子进程树）...")
        for p in list(self.active_procs):
            if p.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(p.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                except Exception:
                    pass
        self.active_procs.clear()
        self.match_connector = None
        self.match_owns_connector = False
        # Also close any UnrealEditor.exe / rviz2.exe belonging to this RMSim workspace.
        cleanup_ps = (
            "$proj = 'd:/simulator/unreal/rmsim/rmsim.uproject'; "
            "Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe' OR Name='rviz2.exe'\" | "
            "Where-Object { $_.Name -eq 'rviz2.exe' -or ($_.CommandLine -and $_.CommandLine.Replace('\\','/').ToLowerInvariant().Contains($proj)) } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cleanup_ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        stop_cmd = REPO_ROOT / "Stop-Navigation.cmd"
        if stop_cmd.is_file():
            subprocess.Popen(["cmd", "/c", str(stop_cmd)], cwd=str(REPO_ROOT))
        self._log("已停止全部 RMSim 仿真与导航进程。")


def main() -> int:
    parser = argparse.ArgumentParser(description="RM Simulator Unified Desktop Launcher")
    parser.add_argument(
        "--mode",
        choices=["match", "pure_ros", "manual", "training", "nav_baseline", "flanders"],
        default="match",
        help="Initial launcher mode (default: match)",
    )
    args, _unknown = parser.parse_known_args()
    app = RMSimLauncherApp(initial_mode=args.mode)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
