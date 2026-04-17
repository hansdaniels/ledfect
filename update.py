import os
import sys
import json
import hashlib
import argparse
import getpass
import io
import tarfile
import urllib.request
import urllib.error
import subprocess
from datetime import datetime, timezone

PACKAGE_TIMEOUT_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 10


def load_env(filepath=".env"):
    env = {}
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip("'").strip('"')
                    env[k.strip()] = v
    return env

def save_env(env, filepath=".env"):
    with open(filepath, "w") as f:
        for key in sorted(env):
            f.write(f"{key}={env[key]}\n")

def is_git_dirty():
    try:
        output = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        return bool(output.strip())
    except Exception:
        return True

def get_git_commit():
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"
    if is_git_dirty():
        return commit + "-dirty"
    return commit

def collect_runtime_files(src_root="src"):
    files = []
    for root, dirs, filenames in os.walk(src_root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".py") or filename == "__init__.py":
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, src_root).replace("\\", "/")
                with open(full_path, "rb") as f:
                    files.append((rel_path, f.read()))
    files.sort(key=lambda item: item[0])
    return files

def compute_content_hash(files):
    hasher = hashlib.sha256()
    for rel_path, content in files:
        hasher.update(rel_path.encode())
        hasher.update(b"\0")
        hasher.update(content)
    return hasher.hexdigest()

def build_release_metadata(files):
    build_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git_commit = get_git_commit()
    content_hash = compute_content_hash(files)
    short_commit = git_commit[:12] if git_commit and git_commit != "unknown" else "unknown"
    version = f"{build_date_utc}-{short_commit}"
    return {
        "version": version,
        "build_date_utc": build_date_utc,
        "git_commit": git_commit,
        "content_hash": content_hash,
    }

def build_package():
    files = collect_runtime_files()
    if not files:
        raise RuntimeError("No runtime files found under src/")

    metadata = build_release_metadata(files)
    manifest = dict(metadata)
    manifest["files"] = [rel_path for rel_path, _ in files] + ["release_metadata.json"]
    manifest["required_files"] = list(manifest["files"])

    release_metadata = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()

    package_buffer = io.BytesIO()
    with tarfile.open(fileobj=package_buffer, mode="w") as archive:
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))

        metadata_info = tarfile.TarInfo("release_metadata.json")
        metadata_info.size = len(release_metadata)
        archive.addfile(metadata_info, io.BytesIO(release_metadata))

        for rel_path, content in files:
            info = tarfile.TarInfo(rel_path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    return package_buffer.getvalue(), metadata

def send_request(url, secret, filepath, content=b"", action="upload"):
    hasher = hashlib.sha256()
    
    if action == "debug":
        hasher.update((secret + "debug").encode())
    elif action == "reset":
        hasher.update((secret + "reset").encode())
    elif action == "package":
        hasher.update((secret + "package").encode())
        hasher.update(content)
    else:
        hasher.update((secret + filepath).encode())
        hasher.update(content)
        
    signature = hasher.hexdigest()
    
    headers = {
        "X-Signature": signature,
        "X-Filepath": filepath
    }
    
    if content:
        headers["Content-Length"] = str(len(content))
        
    req = urllib.request.Request(url, data=content if content else b"", headers=headers, method="POST")
    timeout = PACKAGE_TIMEOUT_SECONDS if action == "package" else DEFAULT_TIMEOUT_SECONDS
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode()
            if response.status == 200:
                if action == "upload":
                    label = filepath
                elif action == "debug":
                    label = "Debug Enable"
                elif action == "reset":
                    label = "Remote Reset"
                else:
                    label = "Package Upload"
                print(f"[OK] {label} -> {res_body}")
                return {"ok": True, "status": response.status, "body": res_body}
            else:
                print(f"[ERROR] {response.status}: {res_body}")
                return {"ok": False, "status": response.status, "body": res_body}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[ERROR] HTTP {e.code}: {body}")
        return {"ok": False, "status": e.code, "body": body}
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return {"ok": False, "status": None, "body": str(e)}

def request_remote_reset(url, secret):
    return send_request(url, secret, "", action="reset")

def build_env_content(env_dict):
    lines = []
    for key in sorted(env_dict):
        lines.append(f"{key}={env_dict[key]}")
    return ("\n".join(lines) + "\n").encode()

def prompt_new_secret():
    first = getpass.getpass("New UPDATE_SECRET: ").strip()
    if not first:
        print("Error: secret must not be empty.")
        return None

    second = getpass.getpass("Confirm new UPDATE_SECRET: ").strip()
    if first != second:
        print("Error: secrets do not match.")
        return None
    return first

def main():
    parser = argparse.ArgumentParser(description="Pico W HTTP Deploy Script")
    parser.add_argument("ip", nargs="?", help="IP address of the Pico W")
    parser.add_argument("--debug", action="store_true", help="Enable WebREPL instead of uploading files")
    parser.add_argument("--set-secret", action="store_true", help="Interactively rotate UPDATE_SECRET and update the local .env after success")
    args = parser.parse_args()

    env = load_env()
    secret = env.get("UPDATE_SECRET")
    pico_ip = args.ip or env.get("PICO_IP")
    webrepl_password = env.get("WEBREPL_PASSWORD")

    if not pico_ip:
        print("Error: IP address required. Pass it as an argument or set PICO_IP in .env")
        sys.exit(1)

    if not secret:
        print("Error: UPDATE_SECRET not found in .env. Uploads will be rejected if the Pico expects one.")
        secret = ""

    if args.debug:
        print(f"Requesting WebREPL enable on {pico_ip}...")
        send_request(f"http://{pico_ip}/api/debug/enable", secret, "", action="debug")
        sys.exit(0)

    if args.set_secret:
        if not env:
            print("Error: local .env not found. Refusing --set-secret because uploading a minimal .env could erase other Pico env values.")
            print("Create a local .env that mirrors the Pico env first, then retry.")
            sys.exit(1)

        if not secret:
            print("Error: UPDATE_SECRET missing from local .env. Cannot authenticate secret rotation.")
            sys.exit(1)

        new_secret = prompt_new_secret()
        if new_secret is None:
            sys.exit(1)

        next_env = dict(env)
        next_env["UPDATE_SECRET"] = new_secret

        if "PICO_IP" not in next_env and pico_ip:
            next_env["PICO_IP"] = pico_ip

        env_content = build_env_content(next_env)

        print(f"Updating UPDATE_SECRET on {pico_ip} via .env upload...")
        ok = send_request(
            f"http://{pico_ip}/api/upload",
            secret,
            ".env",
            env_content,
            action="upload",
        )
        if ok["ok"]:
            save_env(next_env)
            print("Requesting remote reboot to apply the rotated secret to all modules...")
            reset_ok = request_remote_reset(
                f"http://{pico_ip}/api/reset",
                secret,
            )
            print("UPDATE_SECRET updated on Pico.")
            print("Local .env updated to the new secret.")
            if not reset_ok["ok"]:
                print("Warning: secret rotation succeeded, but remote reboot failed.")
            sys.exit(0)
        sys.exit(1)

    uploads_ok = True
    uploaded_anything = False

    try:
        package_bytes, metadata = build_package()
    except Exception as e:
        print(f"Error building release package: {e}")
        sys.exit(1)

    print(
        "Uploading package {} ({}, commit {}, hash {}) to {}...".format(
            metadata["version"],
            metadata["build_date_utc"],
            metadata["git_commit"][:12],
            metadata["content_hash"][:16],
            pico_ip,
        )
    )
    ok = send_request(
        f"http://{pico_ip}/api/upload-package",
        secret,
        "",
        package_bytes,
        action="package",
    )
    uploads_ok = uploads_ok and ok["ok"]
    uploaded_anything = True

    if not ok["ok"]:
        print("\nPackage upload failed. Skipping any follow-up uploads or reboot.")
        sys.exit(1)

    if webrepl_password:
        print("\n-> webrepl_cfg.py (Generated) ...", end=" ")
        cfg_content = f"PASS = '{webrepl_password}'\n".encode()
        ok = send_request(f"http://{pico_ip}/api/upload", secret, "webrepl_cfg.py", cfg_content, action="upload")
        uploads_ok = uploads_ok and ok["ok"]
        uploaded_anything = True

    if uploaded_anything and uploads_ok:
        print("\nAll uploads succeeded. Requesting remote reboot...")
        reset_result = request_remote_reset(f"http://{pico_ip}/api/reset", secret)
        if reset_result["ok"]:
            sys.exit(0)
        if reset_result["status"] == 404:
            print("Remote reset failed, reboot the Pico manually.")
            sys.exit(0)
        sys.exit(1)
    elif uploaded_anything and not uploads_ok:
        print("\nOne or more uploads failed. Skipping remote reboot to avoid booting a partial update.")
        sys.exit(1)

if __name__ == "__main__":
    main()
