import os
import sys
from datetime import datetime
import pwd
from filelock import FileLock
import subprocess
import tempfile
import shutil

# -----------------------------
# Configuration
# -----------------------------
# Home directories root (on Linux, usually /home)
home_root = "/home"
# For root user, we also check /root
additional_users = ["/root"]

# Log file
log_file = "/var/log/ssh_key_cleanup.log"#record information of all expired key

# Current time
now = datetime.now()

# -----------------------------
# Helper: get all user home dirs
# -----------------------------
user_dirs = []
for p in pwd.getpwall():   # pwd.getpwall() returns a list, where each element is a struct_passwd object, including user information.
    # skip system users with no shell
    if p.pw_shell in ("/bin/false", "/usr/sbin/nologin", ""):
        continue
    if p.pw_dir.startswith(home_root) or p.pw_dir in additional_users:
        user_dirs.append(p.pw_dir)

# -----------------------------
# Main cleaning loop
# -----------------------------
for user_dir in user_dirs: # Iterate through all user files
    key_file = os.path.join(user_dir, ".ssh", "authorized_keys") # get complete path of file that include public key
    if not os.path.exists(key_file): # in case that some user don't have file of .ssh/authorized_keys
        continue

    lock_path = key_file + ".lock" #Create a lock file path for the authorized_keys file to prevent
                                # multiple processes from modifying the same file simultaneously.
    with FileLock(lock_path):
        # Backup with timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S") #format datetime into a string
        backup_file = f"{key_file}.bak.{timestamp}" # assign name of back file
        shutil.copy2(key_file, backup_file) # copy from key_file to backup_file

        # Read all lines
        with open(key_file, "r") as f:
            lines = f.readlines()

        new_lines = [] # used to store unexpired key and other information in the key_file
        for line in lines:
            stripped = line.strip()

            # Keep comments and empty lines
            if stripped.startswith("#") or not stripped:
                new_lines.append(line)
                continue

            # Check expiry-time="YYYY-MM-DD"
            if 'expiry-time="' in stripped:
                try:
                    start = stripped.index('expiry-time="') + len('expiry-time="')
                    end = stripped.index('"', start)
                    expiry_str = stripped[start:end]
                    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")

                    if expiry_date < now: # if expired, no append to new_lines which means expired key is deleted
                        print(f"Expired key removed for {user_dir}: {stripped}", file=sys.stdout)
                        with open(log_file, "a") as log: #adding expired key information to log_file
                            log.write(f"{datetime.now()} - Expired key removed for {user_dir}: {stripped}\n")
                    else:
                        new_lines.append(line)
                except Exception: # including ValueError、IndexError、KeyError、TypeError etc.
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(key_file)) # create a temp file that has same path as key_file
        with os.fdopen(tmp_fd, 'w') as tmp_file:
            tmp_file.writelines(new_lines) # write new_lines into temp file
        shutil.move(tmp_path, key_file) # replace key_file with tmp_path (atomic)

# -----------------------------
# Register cron job
# -----------------------------
cron_line = (f"0 2 * * * /usr/bin/python3 '{os.path.abspath(__file__)}' >> "
             f"/var/log/ssh_key_cleanup_cron.log 2>&1\n")

# run before cron job is added so we can prevent duplicate entries, which would cause the script
# to run multiple times at the same scheduled time
result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
current_cron = result.stdout if result.returncode == 0 else ""

#if cron job is empty, then add cron job into job list
if cron_line not in current_cron:
    new_cron = current_cron + cron_line
    subprocess.run(["crontab", "-"], input=new_cron, text=True)
    print("Cron job registered for daily execution at 2:00 AM")



