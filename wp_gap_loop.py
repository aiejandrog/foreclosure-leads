"""Resume-safe WP --gap batch loop. Stops on empty queue, auth fail, or error wall."""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = int(os.environ.get('WP_BATCH', '25'))
MAX_BATCHES = int(os.environ.get('WP_MAX_BATCHES', '50'))
# Fail the loop if a batch finishes with mostly errors (API wall)
ENV = os.environ.copy()
ENV['WP_CONCURRENCY'] = ENV.get('WP_CONCURRENCY', '1')
ENV['WP_THROTTLE_S'] = ENV.get('WP_THROTTLE_S', '2.5')   # trial keys 429 hard; be polite
ENV['WP_MAX_CALLS_PER_RUN'] = str(BATCH)

def run_batch(n):
    print(f'\n======== BATCH {n} limit={BATCH} ========', flush=True)
    p = subprocess.run(
        [sys.executable, 'whitepages_lookup.py', '--gap', '--limit', str(BATCH)],
        cwd=HERE, env=ENV,
    )
    return p.returncode

def remaining():
    p = subprocess.run(
        [sys.executable, 'whitepages_lookup.py', '--stats'],
        cwd=HERE, env=ENV, capture_output=True, text=True,
    )
    print(p.stdout, flush=True)
    for line in (p.stdout or '').splitlines():
        if '--gap remaining:' in line:
            # '--gap remaining: N (...'
            try:
                return int(line.split(':', 1)[1].strip().split()[0])
            except Exception:
                pass
    return None

def main():
    left = remaining()
    print(f'starting gap loop; remaining={left}', flush=True)
    empty_streak = 0
    for i in range(1, MAX_BATCHES + 1):
        left = remaining() if i > 1 else left
        if left is not None and left <= 0:
            print('GAP EMPTY — done', flush=True)
            break
        rc = run_batch(i)
        if rc == 2:
            print('AUTH FAIL — stop', flush=True)
            sys.exit(2)
        if rc == 3:
            print('429 WALL — cooling 180s then continuing', flush=True)
            time.sleep(180)
            continue
        if rc != 0:
            print(f'batch rc={rc}; sleeping 90s then retry once', flush=True)
            time.sleep(90)
            rc = run_batch(i)
            if rc != 0:
                print('API WALL / batch failures — stop for resume', flush=True)
                sys.exit(rc)
        # polite pause between batches
        time.sleep(15)
        left2 = remaining()
        if left is not None and left2 is not None and left2 >= left:
            empty_streak += 1
            print(f'no progress ({left} -> {left2}) streak={empty_streak}', flush=True)
            if empty_streak >= 2:
                print('stuck — stop for resume', flush=True)
                break
        else:
            empty_streak = 0
        if left2 == 0:
            print('GAP EMPTY — done', flush=True)
            break
    print('loop exit; resume with: python wp_gap_loop.py', flush=True)

if __name__ == '__main__':
    main()
