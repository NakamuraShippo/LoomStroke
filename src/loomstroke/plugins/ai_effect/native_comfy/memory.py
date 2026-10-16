"""Request model unloading through the current frontend's authenticated API."""

FREE_MEMORY = r"""
import('./scripts/api.js').then(async ({api}) => {
  const queueResponse = await api.fetchApi('/queue');
  if (!queueResponse.ok) throw new Error(`Queue check failed: HTTP ${queueResponse.status}`);
  const queue = await queueResponse.json();
  if (!Array.isArray(queue.queue_running) || !Array.isArray(queue.queue_pending))
    throw new Error('Invalid ComfyUI queue response');
  if (queue.queue_running.length || queue.queue_pending.length) return 'busy';
  const response = await api.fetchApi('/free', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({unload_models: true, free_memory: true})
  });
  if (!response.ok) throw new Error(`Memory release failed: HTTP ${response.status}`);
  return 'requested';
})
"""
