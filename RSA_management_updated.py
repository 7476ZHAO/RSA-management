#!/usr/bin/env python3
import os
import sys
import argparse
from datetime import datetime, timedelta
import pwd
from filelock import FileLock
import subprocess
import tempfile
import shutil
import re

# -----------------------------
# Configuration
# -----------------------------
home_root = "/home"  # Home directories root (on Linux, usually /home)
additional_users = ["/root"]  # For root user, we also check /root
log_file = "/var/log/ssh_key_cleanup.log"  # record information of all expired key
DEFAULT_EXPIRY = "2h"  # default expiry: today + 2 hours


# -----------------------------
# Helper: parse expiry string into datetime
# -----------------------------
def parse_expiry(expiry_str):
    """
    Try to parse expiry string. Supports:
    - N d / h / m / s (e.g., 2d5h30m10s)
    - YYYY-MM-DD
    - YYYY-MM-DDTHH:MM
    """
    # Regex match formats like 2d5h30m10s
    match = re.findall(r"(\d+)([dhms])", expiry_str)
    if match:
        days = hours = minutes = seconds = 0
        for value, unit in match:
            if unit == "d":
                days += int(value)
            elif unit == "h":
                hours += int(value)
            elif unit == "m":
                minutes += int(value)
            elif unit == "s":
                seconds += int(value)
        return datetime.now() + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

    # Try explicit datetime formats
    try:
        if "T" in expiry_str:
            return datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M")
        return datetime.strptime(expiry_str, "%Y-%m-%d")
    except Exception:
        return None


# -----------------------------
# Helper: get all user home dirs
# -----------------------------
def get_user_dirs():
    """
    Get all user home directories, excluding system users.
    pwd.getpwall() returns a list of accounts information on server,
    where each element is a struct_passwd object, including user information.
    """
    user_dirs = []
    for p in pwd.getpwall():
        # skip system users with no shell
        if p.pw_shell in ("/bin/false", "/usr/sbin/nologin", ""):
            continue
        if p.pw_dir.startswith(home_root) or p.pw_dir in additional_users:
            user_dirs.append(p.pw_dir)
    return user_dirs


# -----------------------------
# INIT: add expiry comment to keys that do not have it
# -----------------------------
def init_keys(expiry_str=DEFAULT_EXPIRY, users=None):
    """
    Add expiry JSON to keys that do not have it.
    expiry_str: string like "2d5h30m" or ISO format "YYYY-MM-DDTHH:MM"
    users: list of user directories (optional). If None, process all users.
    """
    expiry_date = parse_expiry(expiry_str)
    if not expiry_date:
        print(f"[ERROR] Invalid expiry format: {expiry_str}")
        sys.exit(1)

    expiry_str_fmt = expiry_date.strftime("%Y-%m-%dT%H:%M")
    user_dirs = users if users else get_user_dirs()

    for user_dir in user_dirs:
        key_file = os.path.join(user_dir, ".ssh", "authorized_keys")  # get complete path of file that includes public key
        if not os.path.exists(key_file):  # in case that some user don't have file of .ssh/authorized_keys
            continue

        new_lines = []
        with open(key_file, "r") as f:
            for line in f:
                stripped = line.strip()
                # Keep comments and empty lines
                if not stripped or stripped.startswith("#"):  # "not stripped" means this line is a blank line
                    new_lines.append(line)
                    continue
                # If no expiry JSON is present, add one at the end of the line
                if '{"expiry":"' not in stripped:
                    line = line.rstrip("\n") + f' {{"expiry":"{expiry_str_fmt}"}}\n'
                    print(f"[INIT] Added expiry to key in {user_dir}, expires at {expiry_str_fmt}")
                new_lines.append(line)

        # Save original ownership and permissions
        st = os.stat(key_file)
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode

        # Atomic write to replace the authorized_keys safely
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(key_file))
        with os.fdopen(tmp_fd, "w") as tmp:
            tmp.writelines(new_lines)
        shutil.move(tmp_path, key_file)

        # Restore original ownership and permissions
        os.chown(key_file, uid, gid)
        os.chmod(key_file, mode)


# -----------------------------
# CLEANUP: remove expired keys
# -----------------------------
def process_key_file(user_dir):
    """
    Cleanup expired keys in a single authorized_keys file.
    """
    username = os.path.basename(user_dir)
    print(f"[CLEANUP] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Checking user: {username}")

    key_file = os.path.join(user_dir, ".ssh", "authorized_keys")  # get complete path of file that includes public key
    if not os.path.exists(key_file):  # in case that some user don't have file of .ssh/authorized_keys
        return

    lock_path = key_file + ".lock"  # Create a lock file path for the authorized_keys file to prevent race condition
    with FileLock(lock_path):
        with open(key_file, "r") as f:
            lines = f.readlines()  # Read all lines of the file and return them as a list

        new_lines = []  # used to store unexpired key and other information in the key_file
        now = datetime.now()

        for line in lines:
            stripped = line.strip()
            # Keep comments and empty lines
            if stripped.startswith("#") or not stripped:  # "not stripped" means this line is a blank line
                new_lines.append(line)
                continue

            # Check for expiry JSON {"expiry":"..."}
            expiry_match = re.search(r'{"expiry":"([^"]+)"}', stripped)
            if expiry_match:
                try:
                    expiry_str = expiry_match.group(1)
                    expiry_date = parse_expiry(expiry_str)

                    # if expired, do not append to new_lines which means expired key is deleted
                    if expiry_date and expiry_date < now:
                        print(f"[CLEANUP] Removed expired key from {user_dir}: {stripped}", file=sys.stdout)
                        with open(log_file, "a") as log:  # adding expired key information to log_file
                            log.write(f"{datetime.now()} - Expired key removed for {user_dir}: {stripped}\n")
                        continue
                except Exception:  # including ValueError、IndexError、KeyError、TypeError etc.
                    pass
            new_lines.append(line)

        # Save original ownership and permissions
        st = os.stat(key_file)
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode

        # Atomic write,"tmp_fd" is file descriptor, "tmp_path" is the full path of the temporary file
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(key_file))  # create a temp file that has same path as key_file
        with os.fdopen(tmp_fd, 'w') as tmp_file:
            tmp_file.writelines(new_lines)  # write new_lines into temp file
        shutil.move(tmp_path, key_file)  # Atomically replace key_file with tmp_path; cleaning is finished

        # Restore original ownership and permissions
        os.chown(key_file, uid, gid)
        os.chmod(key_file, mode)


# -----------------------------
# Register cron job
# -----------------------------
def register_cron():
    """
    Register a cron job that runs cleanup every 2 minutes.
    """
    cron_line = (f"*/2 * * * * /usr/bin/python3 '{os.path.abspath(__file__)}' cleanup >> "
                 f"/var/log/ssh_key_cleanup_cron.log 2>&1\n")

    # run before cron job is added so we can prevent duplicate entries,
    # which would cause the script to run multiple times at the same scheduled time
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    current_cron = result.stdout if result.returncode == 0 else ""

    # if cron job is empty, then add cron job into job list
    if cron_line not in current_cron:
        new_cron = current_cron + cron_line
        subprocess.run(["crontab", "-"], input=new_cron, text=True)
        print("Cron job registered for execution every 2 minutes")


# -----------------------------
# Main
# -----------------------------
def main():
    """
    Main entry point.
    Supports:
    - init: add expiry comment to all keys
    - cleanup: remove expired keys
    - register-cron: add cron job for cleanup
    """
    parser = argparse.ArgumentParser(description="Manage SSH authorized_keys with expiry comment")
    parser.add_argument("mode", choices=["init", "cleanup", "register-cron"], help="Mode of operation")
    parser.add_argument("--expiry", default=DEFAULT_EXPIRY,
                        help="Expiry duration (e.g. 2d5h30m10s or 2025-12-31T23:59)")
    parser.add_argument("--user", help="Specify a single username to operate on (default: all users)")
    args = parser.parse_args()

    if args.user:
        user_dir = os.path.join(home_root, args.user) if args.user != "root" else "/root"
        if not os.path.exists(user_dir):
            print(f"[ERROR] User {args.user} does not exist or has no home directory")
            sys.exit(1)

        if args.mode == "init":
            init_keys(expiry_str=args.expiry, users=[user_dir])
        elif args.mode == "cleanup":
            process_key_file(user_dir)
        elif args.mode == "register-cron":
            register_cron()
        return

    # Handle all users
    if args.mode == "init":
        init_keys(expiry_str=args.expiry)
    elif args.mode == "cleanup":
        for user_dir in get_user_dirs():  # Iterate through each user's home directory
            process_key_file(user_dir)
    elif args.mode == "register-cron":
        register_cron()
    else:
        print("Unknown mode. Use 'init' or 'cleanup' or 'register-cron'.")


if __name__ == "__main__":
    main()
