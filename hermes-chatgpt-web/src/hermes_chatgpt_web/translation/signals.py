"""
Shared asyncio event for waking the translation worker
when a new job is created, instead of polling.
"""

import asyncio

# Worker awaits this event; routes set it after creating a job.
job_notify = asyncio.Event()
