"""
Generates the BPMN 2.0 files for the two processes this platform actually runs.

Written as a generator rather than hand edited XML for one reason: the diagram
and the process should not drift apart. If the incident flow changes, this file
changes and the .bpmn is regenerated, which is the same argument the rest of the
project makes about lineage and metric definitions.

The output opens in Camunda Modeler and carries diagram interchange, so it
renders rather than arriving as a blank canvas.

Run from the repo root:
    python process/bpmn/generate.py
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

W, H = 150, 90          # task box
GW = 50                 # gateway
EV = 36                 # event
COL = 210               # horizontal spacing
ROW = 150               # vertical spacing between lanes
Y0 = 120


def node(kind, ident, name, col, row=0):
    return {"kind": kind, "id": ident, "name": name, "col": col, "row": row}


def geom(n):
    x = 120 + n["col"] * COL
    y = Y0 + n["row"] * ROW
    if n["kind"] in ("start", "end"):
        return x, y + (H - EV) / 2, EV, EV
    if n["kind"] == "gateway":
        return x, y + (H - GW) / 2, GW, GW
    return x, y, W, H


def build(process_id, name, nodes, flows, path):
    els, di = [], []
    for n in nodes:
        k, i, nm = n["kind"], n["id"], n["name"]
        if k == "start":
            els.append(f'    <bpmn:startEvent id="{i}" name="{nm}" />')
        elif k == "end":
            els.append(f'    <bpmn:endEvent id="{i}" name="{nm}" />')
        elif k == "gateway":
            els.append(f'    <bpmn:exclusiveGateway id="{i}" name="{nm}" />')
        else:
            els.append(f'    <bpmn:task id="{i}" name="{nm}" />')

    for j, (src, tgt, label) in enumerate(flows, 1):
        lbl = f' name="{label}"' if label else ""
        els.append(f'    <bpmn:sequenceFlow id="flow_{j}"{lbl} '
                   f'sourceRef="{src}" targetRef="{tgt}" />')

    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        x, y, w, h = geom(n)
        di.append(f'      <bpmndi:BPMNShape id="{n["id"]}_di" bpmnElement="{n["id"]}">\n'
                  f'        <dc:Bounds x="{int(x)}" y="{int(y)}" width="{w}" height="{h}" />\n'
                  f'      </bpmndi:BPMNShape>')
    for j, (src, tgt, _) in enumerate(flows, 1):
        sx, sy, sw, sh = geom(by_id[src])
        tx, ty, tw, th = geom(by_id[tgt])
        p1 = (int(sx + sw), int(sy + sh / 2))
        p2 = (int(tx), int(ty + th / 2))
        if tx < sx:                      # a loop back, route under the row
            mid = int(max(sy + sh, ty + th) + 50)
            pts = [(int(sx + sw / 2), int(sy + sh)), (int(sx + sw / 2), mid),
                   (int(tx + tw / 2), mid), (int(tx + tw / 2), int(ty + th))]
        else:
            pts = [p1, p2]
        wp = "".join(f'\n          <di:waypoint x="{a}" y="{b}" />' for a, b in pts)
        di.append(f'      <bpmndi:BPMNEdge id="flow_{j}_di" bpmnElement="flow_{j}">{wp}\n'
                  f'      </bpmndi:BPMNEdge>')

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="defs_{process_id}"
                  targetNamespace="http://metrictrace/process">
  <bpmn:process id="{process_id}" name="{name}" isExecutable="false">
{chr(10).join(els)}
  </bpmn:process>
  <bpmndi:BPMNDiagram id="diagram_{process_id}">
    <bpmndi:BPMNPlane id="plane_{process_id}" bpmnElement="{process_id}">
{chr(10).join(di)}
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
'''
    path.write_text(xml, encoding="utf-8")
    print(f"written  {path.relative_to(HERE.parents[1])}  "
          f"({len(nodes)} elements, {len(flows)} flows)")


# --------------------------------------------------------------------------
# 1. Incident process. This is what quality/gate.py already does in code.
# --------------------------------------------------------------------------
incident_nodes = [
    node("start",   "i_start", "Quality check fails",              0),
    node("task",    "i_block", "Block the series\\nfacts are not republished", 1),
    node("gateway", "i_dupe",  "Incident already\\nopen for this\\nseries and check?", 2),
    node("task",    "i_open",  "Open incident\\nattach the runbook",  3),
    node("task",    "i_note",  "Append to the\\nexisting incident",   3, 1),
    node("gateway", "i_known", "Cause covered\\nby a runbook?",       4),
    node("task",    "i_fix",   "Apply the runbook",                   5),
    node("task",    "i_write", "Investigate, then write\\nthe runbook now", 5, 1),
    node("task",    "i_rerun", "Re-run gate\\nand dimensional",        6),
    node("gateway", "i_pass",  "Series publishes?",                   7),
    node("task",    "i_close", "Close incident\\nrecord the resolution", 8),
    node("end",     "i_end",   "Series trusted again",                9),
]
incident_flows = [
    ("i_start", "i_block", ""),
    ("i_block", "i_dupe",  ""),
    ("i_dupe",  "i_open",  "no"),
    ("i_dupe",  "i_note",  "yes, one per series and check"),
    ("i_open",  "i_known", ""),
    ("i_note",  "i_known", ""),
    ("i_known", "i_fix",   "yes"),
    ("i_known", "i_write", "no, runbook written now"),
    ("i_fix",   "i_rerun", ""),
    ("i_write", "i_rerun", ""),
    ("i_rerun", "i_pass",  ""),
    ("i_pass",  "i_close", "yes"),
    ("i_pass",  "i_known", "no"),
    ("i_close", "i_end",   ""),
]

# --------------------------------------------------------------------------
# 2. Metric definition change. This is what process/change.py enforces.
# --------------------------------------------------------------------------
change_nodes = [
    node("start",   "c_start",  "Change proposed to\\nsemantic/metrics.yaml", 0),
    node("task",    "c_status", "Run change status\\ndiff against approved",  1),
    node("gateway", "c_bump",   "Version and\\nlast_changed\\nbumped?",       2),
    node("task",    "c_reject", "Refuse.\\nBump the version first",           3, 1),
    node("task",    "c_impact", "Run impact analysis\\nthrough the lineage table", 3),
    node("task",    "c_review", "Owner reviews\\nwhat reads this metric",     4),
    node("gateway", "c_ok",     "Approved?",                                  5),
    node("task",    "c_record", "Record approver,\\nreason and impact",       6),
    node("task",    "c_log",    "Append entry to\\nprocess/change_log.md",    7),
    node("task",    "c_notify", "Notify owners of\\nevery downstream surface", 8),
    node("end",     "c_end",    "Definition in force",                        9),
    node("end",     "c_drop",   "Change withdrawn",                           6, 1),
]
change_flows = [
    ("c_start",  "c_status", ""),
    ("c_status", "c_bump",   ""),
    ("c_bump",   "c_reject", "no"),
    ("c_bump",   "c_impact", "yes"),
    ("c_reject", "c_start",  "resubmit"),
    ("c_impact", "c_review", ""),
    ("c_review", "c_ok",     ""),
    ("c_ok",     "c_record", "yes"),
    ("c_ok",     "c_drop",   "no"),
    ("c_record", "c_log",    ""),
    ("c_log",    "c_notify", ""),
    ("c_notify", "c_end",    ""),
]

if __name__ == "__main__":
    build("incident_management", "MetricTrace incident management",
          incident_nodes, incident_flows, HERE / "incident_management.bpmn")
    build("metric_change", "MetricTrace metric definition change",
          change_nodes, change_flows, HERE / "metric_change.bpmn")
