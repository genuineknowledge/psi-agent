#!/usr/bin/env python3
"""Redis RDB/AOF persistence recovery time benchmark harness.
Usage: python3 redis_persistence_bench.py --mode rdb|aof --keys 10000 --port 6379
Outputs JSON to stdout with benchmark results.
"""
import argparse, json, os, shutil, signal, subprocess, sys, tempfile, time

REDIS_SERVER = "/tmp/redis-7.2.5/src/redis-server"
REDIS_CLI = "/tmp/redis-7.2.5/src/redis-cli"

def wait_for_redis(port, timeout=30):
    """Wait until redis-server is ready to accept connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [REDIS_CLI, "-p", str(port), "PING"],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and "PONG" in r.stdout:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        time.sleep(0.05)
    return False

def generate_config(mode, port, data_dir):
    """Generate a redis.conf for the given persistence mode."""
    conf = [
        f"port {port}",
        "daemonize no",
        f"dir {data_dir}",
        "loglevel warning",
        "save ''",  # disable default RDB saves
        "appendonly no",
    ]
    if mode == "rdb":
        # Manual save only
        conf.append("save ''")
    elif mode == "aof":
        conf.append("appendonly yes")
        conf.append("appendfsync everysec")
        conf.append("auto-aof-rewrite-percentage 0")  # disable auto rewrite during test
    return "\n".join(conf) + "\n"

def start_redis(config_path: str, port: int) -> subprocess.Popen:
    """Start redis-server with given config. Returns Popen handle."""
    proc = subprocess.Popen(
        [REDIS_SERVER, config_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid
    )
    if not wait_for_redis(port, timeout=15):
        proc.kill()
        raise RuntimeError(f"Redis did not start on port {port}")
    return proc

def stop_redis(proc, port):
    """Gracefully shutdown redis."""
    try:
        subprocess.run([REDIS_CLI, "-p", str(port), "SHUTDOWN", "NOSAVE"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)

def load_data(port, n_keys, value_size=256):
    """Load N keys via pipelined SET commands. Returns elapsed seconds."""
    value = "x" * value_size
    lines = []
    for i in range(n_keys):
        lines.append(f"SET key:{i} {value}")
    input_data = "\n".join(lines).encode()

    t0 = time.perf_counter()
    r = subprocess.run(
        [REDIS_CLI, "-p", str(port), "--pipe"],
        input=input_data, capture_output=True, timeout=300
    )
    elapsed = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError(f"Data load failed: {r.stderr.decode()}")
    return elapsed

def trigger_save(port, mode):
    """Trigger a persistence save and wait for completion."""
    if mode == "rdb":
        subprocess.run([REDIS_CLI, "-p", str(port), "BGSAVE"],
                       capture_output=True, timeout=5)
        # Wait for BGSAVE to complete
        while True:
            r = subprocess.run(
                [REDIS_CLI, "-p", str(port), "INFO", "persistence"],
                capture_output=True, text=True, timeout=5
            )
            if "rdb_bgsave_in_progress:0" in r.stdout:
                break
            time.sleep(0.1)
    elif mode == "aof":
        # Force AOF rewrite to ensure complete AOF
        subprocess.run([REDIS_CLI, "-p", str(port), "BGREWRITEAOF"],
                       capture_output=True, timeout=5)
        while True:
            r = subprocess.run(
                [REDIS_CLI, "-p", str(port), "INFO", "persistence"],
                capture_output=True, text=True, timeout=5
            )
            if "aof_rewrite_in_progress:0" in r.stdout:
                break
            time.sleep(0.1)

def get_dump_size(data_dir, mode):
    """Get the persistence file size in bytes (total of all persistence files)."""
    total = 0
    if mode == "rdb":
        patterns = ["dump.rdb"]
    else:
        patterns = ["appendonly", ".aof", ".rdb"]
    for fname in os.listdir(data_dir):
        fpath = os.path.join(data_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if mode == "rdb" and fname == "dump.rdb":
            total += os.path.getsize(fpath)
        elif mode == "aof" and ("aof" in fname.lower() or fname == "dump.rdb"):
            total += os.path.getsize(fpath)
    return total

def measure_recovery(port, config_path, data_dir):
    """Start redis with existing data and measure time until ready."""
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [REDIS_SERVER, config_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid
    )
    if not wait_for_redis(port, timeout=120):
        proc.kill()
        raise RuntimeError(f"Redis recovery failed on port {port}")
    elapsed = time.perf_counter() - t0
    # Verify key count
    r = subprocess.run(
        [REDIS_CLI, "-p", str(port), "DBSIZE"],
        capture_output=True, text=True, timeout=5
    )
    key_count = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
    stop_redis(proc, port)
    return elapsed, key_count

def run_benchmark(mode, n_keys, port):
    """Run a complete benchmark cycle."""
    data_dir = tempfile.mkdtemp(prefix=f"redis_{mode}_")
    config_path = os.path.join(data_dir, "redis.conf")

    try:
        # Generate config and write it
        conf = generate_config(mode, port, data_dir)
        with open(config_path, "w") as f:
            f.write(conf)

        # Phase 1: Start Redis with empty data
        proc = start_redis(config_path, port)

        # Phase 2: Load data
        load_time = load_data(port, n_keys)

        # Phase 3: Trigger persistence save
        trigger_save(port, mode)

        # Phase 4: Get dump size
        dump_size = get_dump_size(data_dir, mode)

        # Phase 5: Stop Redis
        stop_redis(proc, port)

        # Phase 6: Measure recovery time
        recovery_time, recovered_keys = measure_recovery(port, config_path, data_dir)

        return {
            "mode": mode,
            "keys": n_keys,
            "load_time_s": round(load_time, 3),
            "save_dump_size_bytes": dump_size,
            "recovery_time_s": round(recovery_time, 3),
            "recovered_keys": recovered_keys,
            "data_dir": data_dir,
        }
    finally:
        # Cleanup
        try:
            subprocess.run(["pkill", "-f", f"redis-server.*:{port}"],
                           capture_output=True)
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(data_dir, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["rdb", "aof"])
    parser.add_argument("--keys", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    # Verify binaries exist
    for path in [REDIS_SERVER, REDIS_CLI]:
        if not os.path.exists(path):
            print(json.dumps({"error": f"Binary not found: {path}"}))
            sys.exit(1)

    result = run_benchmark(args.mode, args.keys, args.port)
    print(json.dumps(result))

if __name__ == "__main__":
    main()
