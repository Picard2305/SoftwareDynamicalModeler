import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

source = Path(r"C:\Users\jmfel\Documents\Python\SoftwareDynamicalModeler\runs\queue-new-arrival-profile-study-002.json")
output_dir = source.parent
with source.open(encoding="utf-8") as stream:
    study = json.load(stream)

profiles = ["a", "b", "c"]
methods = [
    ("reflected_train_estimated_capacity", "Reflected (training-estimated)", "#0072B2"),
    ("reflected_configured_capacity_reference", "Reflected (configured reference)", "#56B4E9"),
    ("sparse_nonnegative_projection", "Sparse (nonnegative projection)", "#D55E00"),
    ("sparse_unconstrained", "Sparse (unconstrained)", "#CC79A7"),
]
folds_by_profile = {
    profile: [fold for fold in study["folds"] if fold["profile"] == profile]
    for profile in profiles
}

figure, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)
metrics = [("mae", "MAE (jobs)"), ("normalized_rmse", "Normalized RMSE")]
base_positions = np.arange(len(profiles), dtype=float)
method_offsets = np.linspace(-0.27, 0.27, len(methods))
replicate_jitters = np.array([-0.045, 0.0, 0.045])

for axis, (metric, ylabel) in zip(axes, metrics):
    for method_index, (key, label, color) in enumerate(methods):
        for profile_index, profile in enumerate(profiles):
            values = [fold["scores"][key][metric] for fold in folds_by_profile[profile]]
            x = base_positions[profile_index] + method_offsets[method_index]
            axis.scatter(
                x + replicate_jitters[:len(values)], values,
                color=color, s=46, alpha=0.78,
                label=label if profile_index == 0 else None,
                zorder=3,
            )
            mean_value = float(np.mean(values))
            axis.plot([x - 0.055, x + 0.055], [mean_value, mean_value], color="black", linewidth=2.2, zorder=4)
    axis.set_xticks(base_positions, [profile.upper() for profile in profiles])
    axis.set_xlabel("Held-out arrival profile")
    axis.set_ylabel(ylabel)
    axis.grid(axis="y", alpha=0.25)
    axis.set_axisbelow(True)

axes[0].set_title("Per-run trajectory error: MAE")
axes[1].set_title("Per-run trajectory error: normalized RMSE")
axes[1].legend(frameon=False, fontsize=9, loc="upper left")
figure.suptitle("Held-out queue trajectory prediction by profile\nPoints are individual runs; black ticks are profile means", fontsize=14)
error_path = output_dir / "queue-new-arrival-profile-study-002-errors.png"
figure.savefig(error_path, dpi=180, bbox_inches="tight")
plt.close(figure)

peak_methods = [
    ("reflected_train_estimated_capacity", "Reflected (training-estimated)", "#0072B2"),
    ("reflected_configured_capacity_reference", "Reflected (configured reference)", "#56B4E9"),
    ("sparse_nonnegative_projection", "Sparse (nonnegative projection)", "#D55E00"),
]
profile_markers = {"a": "o", "b": "s", "c": "^"}
figure, axis = plt.subplots(figsize=(8, 7), constrained_layout=True)
observed_values = [fold["observed_peak_queue"] for fold in study["folds"]]
low = min(observed_values) - 10
high = max(observed_values) + 10
axis.plot([low, high], [low, high], linestyle="--", color="0.35", linewidth=1.5, label="Perfect prediction")
for key, label, color in peak_methods:
    for profile in profiles:
        selected = folds_by_profile[profile]
        axis.scatter(
            [fold["observed_peak_queue"] for fold in selected],
            [fold["scores"][key]["predicted_peak_queue"] for fold in selected],
            color=color,
            marker=profile_markers[profile],
            s=72,
            alpha=0.85,
            label=label if profile == profiles[0] else None,
            zorder=3,
        )
axis.set_xlim(low, high)
axis.set_ylim(low, high)
axis.set_aspect("equal", adjustable="box")
axis.set_xlabel("Observed peak queue (jobs)")
axis.set_ylabel("Predicted peak queue (jobs)")
axis.set_title("Peak queue prediction on held-out runs")
axis.grid(alpha=0.25)
legend_handles = [
    Line2D([0], [0], color=color, marker="o", linestyle="None", label=label)
    for _, label, color in peak_methods
]
legend_handles.extend(
    Line2D([0], [0], color="0.25", marker=marker, linestyle="None", label=f"Profile {profile.upper()}")
    for profile, marker in profile_markers.items()
)
legend_handles.append(Line2D([0], [0], color="0.35", linestyle="--", label="Perfect prediction"))
axis.legend(handles=legend_handles, frameon=False, fontsize=9, loc="upper left")
peak_path = output_dir / "queue-new-arrival-profile-study-002-peaks.png"
figure.savefig(peak_path, dpi=180, bbox_inches="tight")
plt.close(figure)

print(error_path)
print(peak_path)
