from prometheus_client import Counter, Histogram

messages_generated = Counter("bot_messages_generated_total", "LLM generated messages")
messages_sent = Counter("bot_messages_sent_total", "Messages successfully sent")
send_failures = Counter("bot_send_failures_total", "Failed send attempts")
cache_hits = Counter("bot_cache_hits_total", "Cache hits")
cache_misses = Counter("bot_cache_misses_total", "Cache misses")
generation_latency = Histogram("bot_generation_seconds", "LLM generation time (s)")
send_latency = Histogram("bot_send_seconds", "Message send time (s)")
