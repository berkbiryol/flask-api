import json
import os
import shutil
import threading
import traceback
import time
from flask import Response, url_for, current_app, after_this_request, \
    copy_current_request_context
from functools import wraps
from uuid import uuid4
# from .utils.tempfiles import MAX_CLEANUP_TRIES

MAX_CLEANUP_TRIES = 5

__all__ = [
    'ApiResult', 'ApiException',
    'ApiFileResult', 'ApiAsyncJob'
]


# Cf https://www.youtube.com/watch?v=1ByQhAM5c1I
class ApiResult:
    def __init__(self, value, status=200):
        self.value = value
        self.status = status

    def to_response(self, serializer=None):
        if serializer:
            r = serializer(self.value)
        else:
            r = self.value

        try:
            j = json.dumps(r)
        except Exception:
            j = repr(r)

        return Response(
            j, status=self.status,
            mimetype='application/json'
        )


class ApiException(ApiResult, Exception):
    def __init__(self, message, status=400):
        self.value = {'success': False, 'message': message}
        self.status = status


KEEP_FILE_FOR_SECONDS = 60


class ApiFileResult(ApiResult):
    def __init__(self, filepath, attachment_name=None, status=200):
        if not os.path.isfile(filepath):
            raise TypeError('Tried to return a non-file as a file')

        root = current_app.root_path
        downloads = os.path.join(root, 'static', 'downloads')
        if not os.path.isdir(downloads):
            os.makedirs(downloads)

        attachment_name = attachment_name or os.path.basename(filepath)

        unique_attachment = attachment_name.rsplit('.', 1)
        unique_attachment[0] += f'_{uuid4().hex}'
        unique_attachment = '.'.join(unique_attachment)

        tmp_file = os.path.join(downloads, unique_attachment)

        # Copy into static dir to serve to client
        shutil.copy2(filepath, tmp_file)

        self.value = {
            'success': True,
            'url': url_for(
                'static', filename=f"downloads/{unique_attachment}"
            ),
            'attachment_name': attachment_name,
            'available_for_seconds': KEEP_FILE_FOR_SECONDS
        }
        self.status = status

        @after_this_request
        def cleanup(response):
            def cleanup_tempfile(tries=0):
                if not os.path.isfile(tmp_file):
                    return
                # Wait for user download to finish
                time.sleep(KEEP_FILE_FOR_SECONDS)
                # Remove tempfile
                try:
                    os.remove(tmp_file)
                except (PermissionError, FileNotFoundError):
                    time.sleep(2)
                    if tries < MAX_CLEANUP_TRIES:
                        cleanup_tempfile(tries=(tries + 1))

            threading.Thread(target=cleanup_tempfile).start()
            return response


class ApiAsyncJob:
    def __init__(self, target, args=(), kwargs={}):
        job_id = str(uuid4())

        @wraps(target)
        @copy_current_request_context
        def async_target(*args, **kwargs):
            try:
                data = {
                    'status': 'complete',
                    'data': target(*args, **kwargs)
                }
            except Exception:
                data = {
                    'status': 'complete',
                    'error': traceback.format_exc()
                }

            if data.get('data') or data.get('error'):
                fpath = job_path(job_id)
                if not os.path.isdir(os.path.dirname(fpath)):
                    os.makedirs(os.path.dirname(fpath))
                with open(fpath, 'w') as f:
                    json.dump(data, f)

        self._thread = threading.Thread(target=async_target, args=args, kwargs=kwargs)
        self._job_id = job_id

    @property
    def job_id(self):
        return self._job_id

    def run(self):
        self._thread.start()
        return self.job_id


def job_path(job_id):
    return os.path.join(current_app.static_folder, f'./temp/{job_id}.json')
