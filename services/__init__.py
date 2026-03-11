"""Domain service layer — canonical mutation pipeline.

All accepted mutations (UI routes, chat tools, imports) flow through these
services. Each service function owns: validation → DB write → cascade → emit.
"""
