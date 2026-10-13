---
title: "SoftwareDynamicalModeler: A Python workflow for system identification in software telemetry"
tags:
  - Python
  - system identification
  - software telemetry
  - dynamical systems
  - SINDy
authors:
  - name: "[Author name]"
    affiliation: 1
affiliations:
  - name: "[Institution or independent researcher, country]"
    index: 1
date: 28 September 2026
bibliography: paper.bib
---

# Summary

Software systems generate telemetry describing both evolving service state and operational inputs. SoftwareDynamicalModeler applies sparse identification of nonlinear dynamics with control inputs (SINDYc) to this setting. Given state measurements $x(t)$ and exogenous inputs $u(t)$, the goal is to identify a compact model of the form

$$\frac{d}{dt}x(t)=f(x(t),u(t)).$$

The software provides a batch workflow for preparing telemetry, fitting candidate equations, and checking whether those equations reproduce observed trajectories under recorded inputs. Users configure which channels are states and which are controls, align measurements with role-aware resampling, and divide observations into deployment, regime, and optional threshold-defined mode windows. Reports include discovered equations, fit metadata, simulation scores, and cross-deployment coefficient comparisons; fitted model sets can be exported as JSON.

The package builds on PySINDy rather than introducing a new sparse-regression algorithm. Its focus is the surrounding workflow: preserving the distinction between state and control channels, avoiding default fits across known deployment boundaries, and recording enough segment and configuration metadata to interpret candidate models. The bundled example uses deterministic synthetic traces with a known delayed workload response. It demonstrates the end-to-end tooling and a held-out episode comparison; it is not an evaluation on production cloud telemetry.

# Statement of need

Software operators commonly collect metrics such as CPU utilization, latency, request rate, queue depth, and replica count, but those traces do not directly provide an inspectable model of how the service state changes under workload and capacity inputs. Researchers and engineers studying performance or operational dynamics need a way to fit equations from telemetry while retaining deployment and operating-regime context. Treating every channel as an interchangeable signal, or pooling observations across software deployments, can obscure the distinction between changing inputs and changing system dynamics.

SoftwareDynamicalModeler is intended for researchers and performance engineers who want to explore sparse dynamical models from historical, multivariate service telemetry. It makes the state/input split configurable, treats deployment boundaries as default segmentation points, supports user-defined operating modes, and uses forward simulation as a model diagnostic alongside derivative-fit statistics. These choices make assumptions visible and produce model artifacts that can be inspected, compared, and reused. The software is a batch identification workflow; it does not provide live monitoring, automatic operational threshold discovery, or multi-service composition.

# State of the field

PySINDy provides Python implementations of sparse identification methods and the core numerical building blocks used by this project [@brunton2016sindy; @brunton2016sindyc; @desilva2020pysindy]. SoftwareDynamicalModeler uses PySINDy optimizers and adds a configurable workflow around them for software telemetry, including role-aware resampling, deployment-aware segmentation, threshold-based modes, forward-simulation scoring, and cross-deployment model comparison. It is therefore a higher-level application workflow, not a replacement for PySINDy; users who need direct control over SINDy algorithms and general dynamical-system research can use PySINDy directly.

Cloud monitoring systems provide collection and visualization of operational metrics, while sparse-identification libraries provide algorithms for fitting dynamical equations. The project connects these concerns for batch analysis of software-system telemetry. Its contribution is the orchestration and metadata needed to apply sparse model fitting to telemetry windows with explicit state/control roles and deployment context. The bundled repository does not establish that this workflow outperforms existing systems, and it contains no documented production deployment study.

# Software design and features

The data model assigns every configured variable a state or control role. State variables are numerically differentiated; control variables, including instance counts, are resampled with zero-order hold and supplied to the learned dynamics as exogenous inputs. This prevents event-like or stepwise operational controls from being treated as smooth state trajectories. CloudWatch retrieval and CSV input are available; the configured CSV path also supports offline and synthetic examples.

Before fitting, observations are divided at deployment boundaries and labeled with a configurable time-of-day regime rule. Optional user-specified thresholds split each segment into modes and lead to a separate fit for each segment/mode pair. This favors explicit, inspectable windows over a single fit that silently spans a deployment change or attempts to infer switching behavior from one large feature library. Automatic changepoint detection and automatic threshold selection are outside the current implementation.

The feature library is tiered: a default linear and domain-term tier, an optional polynomial and interaction tier, and a separately fit Fourier tier for seasonal structure. A condition-number warning exposes potential collinearity. Derivative estimation and sparse optimizer are configurable, and each sparsity threshold yields a candidate rather than being selected solely by in-sample derivative fit. Applicable candidates are simulated with RK4 using observed, zero-order-held controls. In the general workflow, the trailing simulation window comes from the segment used for fitting, so this score is a trajectory diagnostic rather than an independent test set. The repository also includes a separate synthetic comparison script with a reserved holdout episode.

# Research impact statement

The repository contains a deterministic synthetic example with three training episodes and a separate holdout episode. In that comparison, the selected candidate was a delayed autoregressive baseline, with normalized RMSE 0.044 on the holdout; this result demonstrates the comparison workflow and does not establish that a SINDy candidate was best. The generated data encode a three-sample delay and are not production telemetry. The repository does not document external adoption, publications using the software, or results from real service traces. The current evidence is therefore a reproducible capability demonstration; broader research impact remains to be established.

# AI usage disclosure

OpenAI Codex (GPT-6) was used to draft and restructure this manuscript from the project documentation and source code, and to summarize the bundled synthetic experiment. A human author must review and edit the draft, independently verify its technical claims and reported results, and confirm this disclosure before submission. The human author(s) remain responsible for the submitted content.

# Acknowledgements

[Add acknowledgements and funding information, or state that there was no external funding.]

# References
