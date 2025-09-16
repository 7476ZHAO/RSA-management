import os
import sys
from datetime import datetime

# List of user home directories
user_dirs = ["/home/alice", "/home/bob", "/root"]

# Get current time to check for expired keys
now = datetime.now()

# Loop through each user's authorized_keys file
for user_dir in user_dirs:
    # Build the full path to the authorized_keys file
    key_file = os.path.join(user_dir, ".ssh", "authorized_keys")
    # check if the path is valid. Skip if the file does not exist
    if not os.path.exists(key_file):
        continue

    # Read all lines from the file
    with open(key_file, "r") as f:
        lines = f.readlines()    # lines is a list which include all lines

    new_lines = []  # Store lines that are not expired
    for line in lines:
        stripped = line.strip()  # Remove leading/trailing spaces and newline

        # Skip comment lines or empty lines and keep them
        if stripped.startswith("#") or not stripped:
            new_lines.append(line)
            continue

        # Check if the line contains 'expiry-time="YYYY-MM-DD"'
        if 'expiry-time="' in stripped:
            try:
                # Find the start index of the expiry date
                start = stripped.index('expiry-time="') + len('expiry-time="')
                # Find the end index of the expiry date (closing quote)
                end = stripped.index('"', start)
                # Extract the expiry date string
                expiry_str = stripped[start:end]
                # Convert the string to a date object
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")

                if expiry_date < now:
                    # If expired, print to console and do not keep in file
                    print(f"Expired key removed for {user_dir}: {stripped}", file=sys.stdout)
                else:
                    # If not expired, keep the line
                    new_lines.append(line)
            except Exception as e:
                # If any error occurs, just keep the line
                new_lines.append(line)
        else:
            # Lines without expiry-time are kept
            new_lines.append(line)

    # Write back all non-expired lines to the authorized_keys file
    with open(key_file, "w") as f:
        f.writelines(new_lines)
