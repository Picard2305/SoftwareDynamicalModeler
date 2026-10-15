import http from "k6/http";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

const targetUrl = __ENV.TARGET_URL;
const runId = __ENV.RUN_ID;
const outputDir = __ENV.OUTPUT_DIR;
const phases = JSON.parse(__ENV.PHASES_JSON || '[{"name":"preflight","start_seconds":0,"duration_seconds":1,"rate_per_second":1,"expected_requests":1}]');
const acceptedJobs = new Counter("accepted_jobs");
const nonAcceptedResponses = new Counter("non_accepted_responses");
const transportErrors = new Counter("transport_errors");
const requestAttemptsStarted = new Counter("request_attempts_started");
const requestAttemptsCompleted = new Counter("request_attempts_completed");

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ["avg", "min", "med", "max", "p(95)"],
  scenarios: Object.fromEntries(
    phases.map((phase) => [
      phase.name,
      {
        executor: "constant-arrival-rate",
        startTime: `${phase.start_seconds}s`,
        duration: `${phase.duration_seconds}s`,
        rate: phase.rate_per_second,
        timeUnit: "1s",
        preAllocatedVUs: 10,
        maxVUs: 30,
      },
    ]),
  ),
};

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

export default function () {
  requestAttemptsStarted.add(1);
  const phase = exec.scenario.name;
  const payload = JSON.stringify({
    run_id: runId,
    job_id: `${runId}-${phase}-${exec.scenario.iterationInTest}`,
    submitted_at: new Date().toISOString(),
    job_type: "cpu",
  });
  const response = http.post(targetUrl, payload, {
    headers: { "Content-Type": "application/json" },
    tags: { phase },
  });
  requestAttemptsCompleted.add(1);
  if (!response || response.status === 0) {
    transportErrors.add(1);
  } else if (response.status === 202) {
    acceptedJobs.add(1);
  } else {
    nonAcceptedResponses.add(1);
  }
}

export function handleSummary(data) {
  const droppedIterations = metricValueOrZero(data, "dropped_iterations", "count");
  const requestsCompleted = metricValueOrZero(data, "http_reqs", "count");
  const summary = {
    run_id: runId,
    phases,
    expected_requests: phases.reduce((total, phase) => total + phase.expected_requests, 0),
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
  const separator = outputDir.endsWith("/") || outputDir.endsWith("\\") ? "" : "/";
  return {
    [`${outputDir}${separator}k6-summary.json`]: JSON.stringify(summary, null, 2),
  };
}
