from types import SimpleNamespace

import orjson


class FakePolicyJobRepository:
    def __init__(self, raw_outputs):
        self.jobs, self.raw_outputs, self.saved = [], raw_outputs, []

    async def jobs_for(self, collection_run_id, *, tenant_id):
        return [j for j in self.jobs if j.collection_run_id == collection_run_id and j.tenant_id == tenant_id]

    async def create_job(self, **values):
        job = SimpleNamespace(id=f"job-{len(self.jobs) + 1}", documents=None, failed=None, transcript=None,
                              token_usage=None, profile_version=None, cache_hits=0, cache_misses=0,
                              iterations=0, error_code=None, error_message=None, finished_at=None, **values)
        self.jobs.append(job)
        return job

    async def get_job(self, job_id, *, tenant_id):
        return next((j for j in self.jobs if j.id == job_id and j.tenant_id == tenant_id), None)

    async def save_job(self, job):
        self.saved.append(job.state)

    async def load_raw_outputs(self, run):
        return self.raw_outputs

    def submit(self, job_id, documents, failed=()):
        job = next(j for j in self.jobs if j.id == job_id)
        job.state, job.documents, job.failed = "SUBMITTED", orjson.dumps(documents).decode(), orjson.dumps(list(failed)).decode()


class FakeNormalizerClient:
    def __init__(self, *, status=200, body=None, error=None, repo=None, documents=(), failed=()):
        self.status, self.body, self.error, self.repo = status, body, error, repo
        self.documents, self.failed, self.payloads = list(documents), list(failed), []

    async def execute(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error
        if self.repo is not None and self.status == 200:
            self.repo.submit(payload["job_id"], self.documents, self.failed)
        body = self.body if self.body is not None else {
            "submitted": True, "cache_hits": 1, "cache_misses": 1, "iterations": 2,
            "profile_version": "policy-normalizer/1", "usage": {"total_tokens": 9},
            "transcript": [{"type": "thinking", "data": {"content": payload["agent_job_token"]}}],
            "failed": self.failed}
        return self.status, body
