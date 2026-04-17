import ujson

BOOT_STATE_FILE = "boot_state.json"
DEFAULT_SLOT = "src"
ALT_SLOT = "src_alt"


def _default_state():
    return {
        "active_slot": None,
        "pending_slot": None,
        "previous_slot": None,
        "boot_attempts": 0,
        "target_version": None,
        "last_good_version": None,
        "active_metadata": None,
        "pending_metadata": None,
    }


def normalize_slot(slot):
    if slot == ALT_SLOT:
        return ALT_SLOT
    return DEFAULT_SLOT


def inactive_slot(slot):
    return ALT_SLOT if normalize_slot(slot) == DEFAULT_SLOT else DEFAULT_SLOT


def load_state():
    state = _default_state()
    try:
        with open(BOOT_STATE_FILE, "r") as f:
            loaded = ujson.load(f)
        if isinstance(loaded, dict):
            state.update(loaded)
    except (OSError, ValueError):
        pass

    state["active_slot"] = _normalize_optional_slot(state.get("active_slot"))
    state["pending_slot"] = _normalize_optional_slot(state.get("pending_slot"))
    state["previous_slot"] = _normalize_optional_slot(state.get("previous_slot"))
    state["boot_attempts"] = int(state.get("boot_attempts") or 0)
    return state


def save_state(state):
    sanitized = _default_state()
    sanitized.update(state)
    sanitized["active_slot"] = _normalize_optional_slot(sanitized.get("active_slot"))
    sanitized["pending_slot"] = _normalize_optional_slot(sanitized.get("pending_slot"))
    sanitized["previous_slot"] = _normalize_optional_slot(sanitized.get("previous_slot"))
    sanitized["boot_attempts"] = int(sanitized.get("boot_attempts") or 0)
    with open(BOOT_STATE_FILE, "w") as f:
        ujson.dump(sanitized, f)


def clear_state():
    try:
        import os
        os.remove(BOOT_STATE_FILE)
    except OSError:
        pass


def choose_boot_slot():
    state = load_state()

    pending = state.get("pending_slot")
    if pending:
        if int(state.get("boot_attempts") or 0) >= 1:
            fallback = state.get("previous_slot") or DEFAULT_SLOT
            state["active_slot"] = normalize_slot(fallback)
            state["pending_slot"] = None
            state["boot_attempts"] = 0
            state["pending_metadata"] = None
            state["target_version"] = state.get("last_good_version")
            save_state(state)
            return state["active_slot"], state

        state["boot_attempts"] = int(state.get("boot_attempts") or 0) + 1
        save_state(state)
        return normalize_slot(pending), state

    active = state.get("active_slot") or DEFAULT_SLOT
    state["active_slot"] = normalize_slot(active)
    return state["active_slot"], state


def mark_boot_success(slot_name):
    slot_name = normalize_slot(slot_name)
    state = load_state()
    if state.get("pending_slot") == slot_name:
        state["active_slot"] = slot_name
        state["pending_slot"] = None
        state["boot_attempts"] = 0
        state["last_good_version"] = state.get("target_version")
        state["active_metadata"] = state.get("pending_metadata")
        state["pending_metadata"] = None
    else:
        state["active_slot"] = slot_name
        state["boot_attempts"] = 0
    save_state(state)


def stage_pending_update(current_slot, target_slot, metadata):
    state = load_state()
    current_slot = normalize_slot(current_slot)
    target_slot = normalize_slot(target_slot)
    state["active_slot"] = current_slot
    state["previous_slot"] = current_slot
    state["pending_slot"] = target_slot
    state["boot_attempts"] = 0
    state["pending_metadata"] = metadata
    state["target_version"] = None if metadata is None else metadata.get("version")
    save_state(state)


def get_status_info(current_slot):
    state = load_state()
    current_slot = normalize_slot(current_slot)
    metadata = state.get("active_metadata") or {}
    if state.get("active_slot") not in (DEFAULT_SLOT, ALT_SLOT):
        state["active_slot"] = current_slot

    info = {
        "active_slot": state.get("active_slot") or current_slot,
        "pending_slot": state.get("pending_slot"),
        "version": metadata.get("version"),
        "build_date_utc": metadata.get("build_date_utc"),
        "git_commit": metadata.get("git_commit"),
        "content_hash": metadata.get("content_hash"),
    }
    if state.get("pending_metadata"):
        info["pending_version"] = state["pending_metadata"].get("version")
    return info


def _normalize_optional_slot(slot):
    if slot in (DEFAULT_SLOT, ALT_SLOT):
        return slot
    return None
