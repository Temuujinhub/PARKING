"""Keep exactly one stream task for each camera's current configuration."""
import asyncio


async def stop_camera_task(tasks, configs, device_id):
    task = tasks.pop(device_id, None)
    configs.pop(device_id, None)
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def ensure_camera_task(tasks, configs, device_id, config, start):
    task = tasks.get(device_id)
    if task is not None and not task.done() and configs.get(device_id) == config:
        return False
    await stop_camera_task(tasks, configs, device_id)
    configs[device_id] = config
    tasks[device_id] = asyncio.create_task(start())
    return True
