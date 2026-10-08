import { describe, it, expect } from "vitest";
import { jobPollInterval } from "./useJob";
import type { Job } from "@/lib/jobsApi";

const job = (status: Job["status"]) => ({ status }) as Job;

describe("jobPollInterval", () => {
  it("polls fast first, then slower, and stops when finished", () => {
    expect(jobPollInterval(job("queued"), 0, 5_000)).toBe(1000);
    expect(jobPollInterval(job("running"), 0, 31_000)).toBe(3000);
    expect(jobPollInterval(job("succeeded"), 0, 1_000)).toBe(false);
    expect(jobPollInterval(undefined, 0, 1_000)).toBe(1000);
  });

  it("stops for every terminal status and keeps polling while active", () => {
    expect(jobPollInterval(job("failed"), 0, 99_000)).toBe(false);
    expect(jobPollInterval(job("cancelled"), 0, 1_000)).toBe(false);
    expect(jobPollInterval(job("running"), 0, 29_999)).toBe(1000);
    expect(jobPollInterval(job("running"), 0, 30_000)).toBe(3000);
  });

  it("stops polling a job that cannot be loaded, but keeps polling after a transient error with data", () => {
    expect(jobPollInterval(undefined, 0, 1_000, true)).toBe(false);
    expect(jobPollInterval(job("running"), 0, 1_000, false)).toBe(1000);
  });
});
