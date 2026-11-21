"""本地权益状态（无授权码、无联网校验）。

2026-11 起产品口径：三档功能完全一致，档位只决定内置知识库范围，
因此这里只保留“档位判定 + 本地状态（试用锚点、严格模式计数、历史标记）”，
不再有激活码、续期、设备绑定或许可服务调用。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from edition import EDITION, IS_OFFLINE, LIMITS

DEV_EDITION = EDITION in ("dev", "enterprise", "buyout")
LIMIT = LIMITS.get(EDITION, LIMITS["free"])

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("YIJIANG_DATA", str(ROOT / "data"))).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA / "entitlement_state.enc"
LICENSE_FILE = DATA / "license.json"
ANCHOR_FILE = DATA / ".anchor"
REG_PATH = r"Software\Yijiang"


def _reg_get() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            out: dict[str, Any] = {}
            for name in ("first_launch_at", "last_seen_at", "strict_day", "strict_count"):
                try:
                    out[name] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
            return out
    except Exception:
        return {}


def _reg_set(**values: Any) -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    except Exception:
        pass


DEV_MODE = EDITION == "dev" or (
    not getattr(sys, "frozen", False) and os.getenv("YIJIANG_DEV") == "1"
)

FREE_RULE_LIMIT = 3
FREE_CASE_LIMIT = 1
FREE_CASE_EN_WORDS = 600
FREE_CASE_ZH_CHARS = 300
STRICT_TRIAL_HOURS = 72
CLOCK_ROLLBACK_SECONDS = 300


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI。仅用于保护本机小文件（状态、API Key），与授权无关。"""
    if os.name != "nt":
        return data
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = _DataBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        args = [ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)]
        if not fn(*args):
            raise OSError("DPAPI operation failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return b"PLAIN:" + data if protect else data.removeprefix(b"PLAIN:")


def load_protected(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        raw = _dpapi(path.read_bytes(), protect=False)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def protect_text(value: str) -> str:
    if not value:
        return value
    try:
        return "enc:" + base64.b64encode(_dpapi(value.encode("utf-8"), protect=True)).decode("ascii")
    except Exception:
        return "enc:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def unprotect_text(value: str) -> str:
    if not value:
        return value
    if value.startswith("enc:"):
        try:
            raw = base64.b64decode(value[4:])
            return _dpapi(raw, protect=False).decode("utf-8")
        except Exception:
            return value
    return value


def save_protected(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _dpapi(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"), protect=True)
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        os.close(fd)
        Path(tmp_name).write_bytes(raw)
        os.replace(tmp_name, path)
    except PermissionError:
        if tmp_name:
            try:
                Path(tmp_name).unlink()
            except Exception:
                pass
        path.write_bytes(raw)


@dataclass
class Entitlements:
    """档位来自构建期 edition，不再有激活码；对外接口保持原样以兼容旧调用点。"""

    state_path: Path = STATE_PATH

    def __post_init__(self) -> None:
        self.persisted = True
        self.state = load_protected(self.state_path, {})
        self._init_state()
        self._check_clock()

    def _init_state(self) -> None:
        changed = False
        now = time.time()
        if "first_launch_at" not in self.state:
            self.state["first_launch_at"] = now
            changed = True
        if "strict_used" not in self.state:
            self.state["strict_used"] = {}
            changed = True
        if "last_seen_at" not in self.state:
            self.state["last_seen_at"] = now
            changed = True
        if not self.state.get("install_id"):
            self.state["install_id"] = secrets.token_hex(8)
            changed = True
        anchor = _reg_get()
        try:
            reg_first = float(anchor.get("first_launch_at") or 0)
            cur_first = float(self.state.get("first_launch_at") or 0)
            if reg_first and (not cur_first or reg_first < cur_first):
                self.state["first_launch_at"] = reg_first
                changed = True
            reg_last = float(anchor.get("last_seen_at") or 0)
            if reg_last > float(self.state.get("last_seen_at") or 0):
                self.state["last_seen_at"] = reg_last
                changed = True
        except Exception:
            pass
        if changed:
            self._save()

    def _save(self, update_last: bool = True) -> None:
        if update_last:
            previous = float(self.state.get("last_seen_at") or 0)
            self.state["last_seen_at"] = max(previous, time.time())
        if DEV_MODE:
            return
        try:
            today = self._today()
            _reg_set(
                first_launch_at=f"{float(self.state.get('first_launch_at') or time.time()):.0f}",
                last_seen_at=f"{time.time():.0f}",
                strict_day=today,
                strict_count=int((self.state.get("strict_used") or {}).get(today, 0)),
            )
        except Exception:
            pass
        try:
            save_protected(self.state_path, self.state)
            self.persisted = True
        except Exception:
            self.persisted = False

    def _check_clock(self) -> None:
        now = time.time()
        last = float(self.state.get("last_seen_at") or now)
        if now + CLOCK_ROLLBACK_SECONDS < last:
            self.state["clock_tampered"] = True
        self._save(update_last=True)

    # ---- 档位 -------------------------------------------------------
    def install_id(self) -> str:
        return str(self.state.get("install_id") or "")

    def tier_name(self) -> str:
        """档位只由构建期 edition 决定。"""
        if EDITION in ("enterprise", "buyout", "dev"):
            return "buyout"
        if EDITION == "lite":
            return "lite"
        if EDITION == "complete":
            return "buyout"
        return "free"

    def limits(self) -> dict[str, int | None]:
        return dict(LIMITS.get(self.tier_name(), LIMITS["free"]))

    @property
    def is_pro(self) -> bool:
        return True

    @property
    def can_view_rules(self) -> bool:
        return True

    def has_translated(self) -> bool:
        return bool(self.state.get("has_translated"))

    def mark_translated(self) -> None:
        if not self.state.get("has_translated"):
            self.state["has_translated"] = True
            self._save()

    @property
    def plan_name(self) -> str:
        return self.tier_name()

    @property
    def first_launch_at(self) -> datetime:
        return datetime.fromtimestamp(float(self.state.get("first_launch_at") or time.time()))

    def effective_now(self) -> datetime:
        last = float(self.state.get("last_seen_at") or 0)
        return datetime.fromtimestamp(max(time.time(), last))

    def _today(self) -> str:
        return self.effective_now().strftime("%Y-%m-%d")

    @property
    def trial_ends_at(self) -> datetime:
        return self.first_launch_at + timedelta(hours=STRICT_TRIAL_HOURS)

    def in_strict_trial(self) -> bool:
        if self.tier_name() != "free":
            return False
        return self.effective_now() < self.trial_ends_at

    def trial_hours_left(self) -> float:
        if self.tier_name() != "free":
            return 0.0
        seconds = (self.trial_ends_at - self.effective_now()).total_seconds()
        return round(max(0.0, seconds / 3600), 1)

    def strict_limit(self) -> int | None:
        return self.limits().get("strict_daily")

    def strict_used_today(self) -> int:
        return int((self.state.get("strict_used") or {}).get(self._today(), 0))

    def strict_remaining_today(self) -> int | None:
        return None

    def strict_allowed(self) -> bool:
        return True

    def can_use_strict(self) -> tuple[bool, str]:
        return True, ""

    def consume_strict(self) -> None:
        return None

    def can(self, feature: str) -> bool:
        return True

    # ---- 兼容旧调用点（已无授权，全部为空实现） --------------------
    def renew_license(self) -> None:
        return None

    def activate_code(self, code: str) -> dict[str, Any]:
        raise ValueError("当前版本不需要激活码")

    def deactivate_code(self) -> None:
        return None

    def public(self) -> dict[str, Any]:
        lim = self.limits()
        return {
            "plan": self.plan_name,
            "tier": self.tier_name(),
            "install_id": self.install_id(),
            "activated": True,
            "license_product": self.tier_name(),
            "license_exp": None,
            "token_exp": None,
            "order_exp": None,
            "last_check_at": None,
            "grace_until": None,
            "needs_renew": False,
            "is_pro": True,
            "can_view_rules": True,
            "source": "builtin",
            "device_code": "",
            "has_translated": self.has_translated(),
            "expires_at": None,
            "trial_ends_at": self.trial_ends_at.isoformat(),
            "trial_hours_left": self.trial_hours_left(),
            "in_strict_trial": self.in_strict_trial(),
            "strict_allowed": True,
            "strict_locked": False,
            "free_rule_limit": lim.get("rules"),
            "free_case_limit": lim.get("cases"),
            "free_case_en_words": FREE_CASE_EN_WORDS,
            "free_case_zh_chars": FREE_CASE_ZH_CHARS,
            "clock_tampered": bool(self.state.get("clock_tampered")),
            "test_build": False,
            "test_expired": False,
            "test_exported": False,
            "strict_remaining_today": None,
            "edition": EDITION,
            "limits": lim,
            "features": {
                "strict": True,
                "review": True,
                "result_rules": True,
                "kb_export": True,
                "usage_detail": True,
                "edit_terms": True,
                "edit_cases": True,
                "add_rules": True,
                "import_case": True,
                "import_terms": True,
                "import_cases_unlimited": True,
                "strict_unlimited": True,
                "import_terms_cases": True,
            },
        }
