import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

const targetUrl = __ENV.TARGET_URL;
const runId = __ENV.RUN_ID;
const jobType = __ENV.JOB_TYPE || "cpu";
const outputDir = __ENV.OUTPUT_DIR;
const k6Version = __ENV.K6_VERSION || null;
const smokeMode = __ENV.SMOKE === "1";
const stepSchedule = [{ name: "step_35", startTime: "0s", duration: "120s", rate: 35, preAllocatedVUs: 75, maxVUs: 150 }];
const smokeSchedule = [
  { name: "smoke", startTime: "0s", duration: "5s", rate: 1, preAllocatedVUs: 1, maxVUs: 2 },
];
const schedule = smokeMode ? smokeSchedule : stepSchedule;
const acceptedJobs = new Counter("accepted_jobs");
const nonAcceptedResponses = new Counter("non_accepted_responses");
const transportErrors = new Counter("transport_errors");
const requestAttemptsStarted = new Counter("request_attempts_started");
const requestAttemptsCompleted = new Counter("request_attempts_completed");

if (!targetUrl) {
  throw new Error("Set TARGET_URL to the complete local /jobs endpoint URL.");
}
if (!/^https?:\/\//i.test(targetUrl)) {
  throw new Error("TARGET_URL must use HTTP or HTTPS.");
}
const authority = targetUrl.match(/^https?:\/\/([^/?#]+)/i);
if (!authority || authority[1].includes("@")) {
  throw new Error("TARGET_URL must not contain URL credentials.");
}
if (!runId) {
  throw new Error("Set RUN_ID to the identifier used by the queueing app.");
}
if (!outputDir) {
  throw new Error("Set OUTPUT_DIR to the unique directory for this run.");
}

function createScenario(phase) {
  return {
    executor: "constant-arrival-rate",
    startTime: phase.startTime,
    duration: phase.duration,
    rate: phase.rate,
    timeUnit: "1s",
    preAllocatedVUs: phase.preAllocatedVUs,
    maxVUs: phase.maxVUs,
  };
}

function createScenarios(phases) {
  const scenarios = {};
  for (let index = 0; index < phases.length; index += 1) {
    const phase = phases[index];
    scenarios[phase.name] = createScenario(phase);
  }
  return scenarios;
}

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ["avg", "min", "med", "max", "p(95)"],
  systemTags: ["status", "method", "scenario"],
  scenarios: createScenarios(schedule),
};

export default function () {
  requestAttemptsStarted.add(1);
  const payload = JSON.stringify({
    run_id: runId,
    job_id: `${runId}-${exec.scenario.name}-${exec.scenario.iterationInTest}`,
    submitted_at: new Date().toISOString(),
    job_type: jobType,
  });
  const response = http.post(targetUrl, payload, {
    headers: { "Content-Type": "application/json" },
    tags: { name: "POST /jobs" },
  });
  requestAttemptsCompleted.add(1);

  if (!response || response.status === 0) {
    transportErrors.add(1);
  } else if (response.status === 202) {
    acceptedJobs.add(1);
  } else {
    nonAcceptedResponses.add(1);
  }

  check(response, { "job accepted": (result) => result !== null && result.status === 202 });
}

function metricValue(data, metricName, valueName) {
  const metric = data.metrics[metricName];
  if (!metric || !metric.values || metric.values[valueName] === undefined) {
    return null;
  }
  return metric.values[valueName];
}

function metricValueOrZero(data, metricName, valueName) {
  const value = metricValue(data, metricName, valueName);
  return value === null ? 0 : value;
}

function outputPath(fileName) {
  const separator = outputDir.endsWith("/") || outputDir.endsWith("\\") ? "" : "/";
  return `${outputDir}${separator}${fileName}`;
}

export function handleSummary(data) {
  const droppedIterations = metricValueOrZero(data, "dropped_iterations", "count");
  const requestsCompleted = metricValueOrZero(data, "http_reqs", "count");
  const summary = {
    run_id: runId,
    job_type: jobType,
    k6_version: k6Version,
    smoke_mode: smokeMode,
    schedule,
    offered_load_complete: droppedIterations === 0,
    iterations_completed: metricValueOrZero(data, "iterations", "count"),
    requests_completed: requestsCompleted,
    request_attempts_started: metricValueOrZero(data, "request_attempts_started", "count"),
    request_attempts_completed: metricValueOrZero(data, "request_attempts_completed", "count"),
    dropped_iterations: droppedIterations,
    outcomes: {
      accepted: metricValueOrZero(data, "accepted_jobs", "count"),
      non_accepted_responses: metricValueOrZero(data, "non_accepted_responses", "count"),
      transport_errors: metricValueOrZero(data, "transport_errors", "count"),
      http_failure_rate: metricValueOrZero(data, "http_req_failed", "rate"),
    },
    latency_ms: {
      count: requestsCompleted,
      mean: metricValue(data, "http_req_duration", "avg"),
      median: metricValue(data, "http_req_duration", "med"),
      p95: metricValue(data, "http_req_duration", "p(95)"),
      max: metricValue(data, "http_req_duration", "max"),
    },
  };
  return {
    [outputPath("k6-summary.json")]: JSON.stringify(summary, null, 2),
  };
}
