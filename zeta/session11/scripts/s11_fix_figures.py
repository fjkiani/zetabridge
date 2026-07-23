"""Regenerate the two figures that failed QC, from saved result JSONs."""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'
import numpy as np

OUT = "/mnt/results/session11"

# ── W0: community sizes — log y, one bar per community (ranked), not a histogram ──
d0 = json.load(open("/mnt/shared-workspace/shared/s11_w0_results.json"))
comm_sizes = d0.get("community_sizes", [])  # list of [cid, size] — top 20 stored
n_total = d0.get("n_communities", len(comm_sizes))
sizes = sorted([s for _, s in comm_sizes], reverse=True)
fig, ax = plt.subplots(figsize=(11, 6))
xs = range(len(sizes))
ax.bar(xs, sizes, color='#0279EE', edgecolor='black', linewidth=0.4)
ax.set_yscale('log')
ax.set_xlabel("Community rank (largest → smallest)")
ax.set_ylabel("Community size (# nodes, log scale)")
ax.set_title(f"Louvain: Top {len(sizes)} of {n_total:,} communities "
             f"(largest={sizes[0]:,} nodes; long tail = {n_total-len(sizes):,} mostly-singleton communities)")
ax.axhline(5, color='#FF9400', linestyle='--', linewidth=1.2, label='Min size threshold (5)')
for i, s in enumerate(sizes[:8]):
    ax.text(i, s, f"{s:,}", ha='center', va='bottom', fontsize=7)
ax.legend()
ax.grid(True, alpha=0.3, axis='y', which='both')
plt.tight_layout()
plt.savefig(f"{OUT}/w0_community_sizes.png", dpi=150, bbox_inches='tight')
plt.savefig(f"{OUT}/w0_community_sizes.svg", bbox_inches='tight')
plt.close()
print(f"[FIX] W0 community_sizes: {len(sizes)} communities, sizes {sizes[:5]}...")

# ── W3: genomic-AE bridge scores — full labels, color by score, wide left margin ──
d3 = json.load(open("/mnt/shared-workspace/shared/s11_w3_results.json"))
bridges = [n for n in d3["new_nodes"] if n["type"] == "GenomicAEBridge"]
bridges = sorted(bridges, key=lambda b: -b["attributes"].get("bridge_score", 0))
labels, vals, recurs = [], [], []
for b in bridges:
    a = b["attributes"]
    ae = a.get("ae_term", "?")
    labels.append(f"{a.get('gene','?')} → {ae}")
    vals.append(a.get("bridge_score", 0))
    recurs.append(a.get("recurrence_pct", 0))
# color by bridge score magnitude (viridis, colorblind-safe)
vmax = max(vals) if vals else 1
norm = [v / vmax for v in vals]
cmap = plt.cm.viridis
colors = [cmap(n) for n in norm]
fig, ax = plt.subplots(figsize=(12, 8))
ypos = range(len(labels))
ax.barh(list(ypos), vals, color=colors, edgecolor='black', linewidth=0.3)
ax.set_yticks(list(ypos))
ax.set_yticklabels(labels, fontsize=8)
ax.set_xlabel("Bridge Score  =  recurrence_pct × max(RR, consistency) / 100")
ax.set_title("Top 20 Cross-Endpoint Genomic-AE Bridges (MSK genomics ↔ SAS clinical AEs)\n"
             "Color = bridge score magnitude; all driver genes recur 2.5% in HGSOC")
ax.invert_yaxis()
# annotate with the signal driver (consistency, since RR=0 for these)
for i, b in enumerate(bridges):
    a = b["attributes"]
    ax.text(vals[i] + vmax*0.01, i,
            f"consist={a.get('consistency_score',0):.1f}",
            va='center', fontsize=7, color='#333333')
ax.set_xlim(0, vmax * 1.25)
sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(vmin=0, vmax=vmax))
sm.set_array([])
cbar = fig.colorbar(sm, ax=ax, pad=0.02)
cbar.set_label("Bridge score", fontsize=8)
plt.tight_layout()
plt.savefig(f"{OUT}/w3_genomic_ae_bridge_scores.png", dpi=150, bbox_inches='tight')
plt.savefig(f"{OUT}/w3_genomic_ae_bridge_scores.svg", bbox_inches='tight')
plt.close()
print(f"[FIX] W3 bridge scores: {len(bridges)} bridges, top={labels[0]} ({vals[0]:.3f})")
print("[FIX] Done.")
