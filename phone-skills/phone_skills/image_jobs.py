"""Durable image-chat requests; relay delivery outlives the uploading client."""
from __future__ import annotations

import hashlib
import json
import threading
import time

from .audio_jobs import AudioJobs
from .image_chat_error import ImageChatError
from .storage import atomic_json


class ImageJobs(AudioJobs):
    WAIT_SECONDS = 120

    def __init__(self, root, service):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.service = service
        self.lock = threading.Lock()
        self.events = {}
        for path in self.root.glob('*.json'):
            state = json.loads(path.read_text(encoding='utf-8'))
            if state['status'] == 'running':
                # Remembering a person may have succeeded before an interruption.
                state.update(status='needs_reconciliation')
                state.pop('request', None)
                atomic_json(path, state)
            elif state['status'] == 'queued':
                self._launch(state['session_id'])

    def submit(self, request):
        if not isinstance(request, dict):
            raise ValueError('invalid_image_request')
        request_id = request.get('request_id', '')
        if not isinstance(request_id, str) or not request_id:
            raise ValueError('invalid_request_id')
        session_id = 'img-' + request_id
        path = self._path(session_id)
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            if path.exists():
                state = json.loads(path.read_text(encoding='utf-8'))
                if state['fingerprint'] != fingerprint:
                    raise ValueError('image_request_conflict')
            else:
                state = dict(session_id=session_id, conversation_id=request.get('conversation_id', ''),
                             status='queued', request=request, fingerprint=fingerprint,
                             created_at=time.time(), notify=True, notify_fast=True)
                atomic_json(path, state)
                self._launch(session_id)
        return {'accepted': True, 'session_id': session_id,
                'answer': '照片收好啦，鲸鲸正在看～结果会单独发来，不用重复发送。',
                'task_status': state['status']}

    def _run(self, session_id):
        path = self._path(session_id)
        try:
            with self.lock:
                state = json.loads(path.read_text(encoding='utf-8'))
                if state['status'] != 'queued':
                    return
                state['status'] = 'running'
                atomic_json(path, state)
            try:
                result = self.service.handle(state['request'])
                reply = result['answer']
                if result.get('profile_updated'):
                    reply += '\n已写入人物卡：' + result.get('profile_person', '')
                elif result.get('remembered'):
                    reply += '\n已保存为长期记忆。'
                result = dict(result, reply=reply)
            except Exception as exc:
                message = exc.message if isinstance(exc, ImageChatError) else '图片处理出现中断，请回来核对，未自动重复执行。'
                result = {'reply': message, 'action': {'status': 'error'}}
            with self.lock:
                state = json.loads(path.read_text(encoding='utf-8'))
                state.update(status='completed', result=result, finished_at=time.time())
                state.pop('request', None)
                atomic_json(path, state)
        finally:
            self.events[session_id].set()
