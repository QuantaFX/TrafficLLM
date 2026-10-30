"""Keep only demand routes SUMO can actually load.

Tries `sumo --end 1` on the generated route file; on failure it parses the
offending (flow, edge) pair out of SUMO's message, drops that destination from
the route, and retries. Emits a clean route file or fails loudly after --tries.
"""
import argparse, re, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("--net", required=True)
ap.add_argument("--routes", required=True)
ap.add_argument("-o", required=True)
ap.add_argument("--tries", type=int, default=80)
a = ap.parse_args()

SUMOLIB_XML = r"C:\Program Files (x86)\Eclipse\Sumo\bin\sumo.exe"

src = open(a.routes).read()
# split into flow blocks: id, then edges
blocks = re.findall(r'(<flow id="([^"]+)".*?<route edges="([^"]*)"/>\n  </flow>)', src, re.S)
remaining = {fid: edges.split() for _blk, fid, edges in blocks}

for attempt in range(a.tries):
    body = []
    for fid, edges in remaining.items():
        body.append(f'  <flow id="{fid}" type="car" begin="0" end="3600" vehsPerHour="80" '
                    f'departLane="free" departSpeed="max">\n'
                    f'    <route edges="{" ".join(edges)}"/>\n  </flow>\n')
    with open(a.o, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
        f.write('  <vType id="car" length="5" minGap="2" accel="2.6" decel="4.5" maxSpeed="13.89"/>\n')
        f.write("".join(body))
        f.write("</routes>\n")
    r = subprocess.run([SUMOLIB_XML, "-n", a.net, "-r", a.o, "--no-warnings", "--no-step-log",
                        "--end", "1", "--time-to-teleport", "-1"], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"clean after {attempt} drops; {len(remaining)} routes left")
        sys.exit(0)
    m = re.search(r"Vehicle '([^']+)' has no valid route\. No connection between edge '([^']+)' and edge '([^']+)'", r.stderr)
    if not m:
        m2 = re.search(r"Vehicle '([^']+)' has no valid route", r.stderr)
        if m2:
            remaining.pop(m2.group(1).rsplit(".", 1)[0], None)
            continue
        print(r.stderr[-600:]); sys.exit(1)
    fid, bad_out, bad_in = m.groups()
    fid = fid.rsplit(".", 1)[0]
    edges = remaining.get(fid)
    if not edges or len(edges) < 2:
        remaining.pop(fid, None); continue
    edges[1:] = [e for e in edges[1:] if e != bad_in]
    if len(edges) < 2:
        remaining.pop(fid, None)
print("gave up after tries"); sys.exit(1)