"""RM Simulator Unified Desktop Launcher (Tkinter).

Provides a single GUI entry point to:
- Pick any vehicle package (built-in or external vehicle.json / vehicle.canonical.json)
- Pick any map package and spawn point
- Choose mode: Pure Simulator + ROS 2 Bridge (external robot plug-in), Manual Drive,
  Training Range, Built-in Navigation Baseline (32.2s Curvature-Profiled MPC 0-overlap μ=1.05), or War Thunder Flanders
- Optionally launch the decoupled external sentry_ws connector against a live simulator
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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
        spawns = [s.get("id", "default") for s in data.get("spawns", []) if isinstance(s, dict)]
        if not spawns:
            spawns = ["default"]
        items.append(
            {
                "id": data.get("id", entry.name),
                "display_name": data.get("display_name", entry.name),
                "path": str(mf),
                "kind": data.get("kind", "unknown"),
                "spawns": spawns,
            }
        )
    return items


class RMSimLauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RM Simulator — 统一仿真启动器")
        self.geometry("860x700")
        self.minsize(780, 640)
        self.configure(bg="#14181f")

        self.vehicles = discover_vehicles()
        self.maps = discover_maps()
        self.active_procs: list[subprocess.Popen] = []

        self._setup_style()
        self._build_ui()
        self._on_vehicle_changed()
        self._on_map_changed()
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
        self.mode_var = tk.StringVar(value="pure_ros")
        modes = [
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

        # 2. Vehicle & Map Selection
        vm_frame = ttk.LabelFrame(body, text=" 2. 车辆包与地图选择（车图解耦） ", style="Card.TLabelframe", padding=10)
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
        cfg_frame = ttk.LabelFrame(body, text=" 3. 物理后端 / ROS 2 外部接入参数 ", style="Card.TLabelframe", padding=10)
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
        ttk.Combobox(cfg_frame, textvariable=self.camera_hz_var, values=["0", "10", "30"], width=10).grid(
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
        ttk.Button(btn_bar, text="▶ 启动仿真", style="Accent.TButton", command=self._launch_simulator).pack(
            side="left", padx=(0, 10)
        )
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

    def _on_mode_changed(self) -> None:
        mode = self.mode_var.get()
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

    def _launch_simulator(self) -> None:
        mode = self.mode_var.get()
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

    def _launch_external_sentry(self) -> None:
        selected_map = self._selected_map()
        if not selected_map:
            messagebox.showwarning("缺少选择", "请先选择仿真器正在使用的地图。")
            return
        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(REPO_ROOT / "tools" / "environment" / "Start-ExternalSentry.ps1"),
            "-RobotId",
            self.robot_id_var.get().strip() or "aim_sentry",
            "-Domain",
            self.domain_var.get().strip() or "71",
            "-NavigationSource",
            self.nav_yaml_var.get().strip(),
            "-MapFile",
            selected_map["path"],
            "-SpawnId",
            self.spawn_var.get(),
        ]
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
    app = RMSimLauncherApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
