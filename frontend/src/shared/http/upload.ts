import { ApiError, getCsrfToken, NETWORK_ERROR, notifyUnauthenticated, resolveUrl, toApiError } from './client';

/**
 * multipart upload with progress (fetch has no upload progress, design D 3.5). Same cookie, CSRF and
 * error handling as apiFetch.
 */
export function uploadFile<T>(path: string, file: File, onProgress?: (fraction: number) => void,
  signal?: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', resolveUrl(path));
    xhr.withCredentials = false; // same-origin: cookies are sent anyway
    xhr.setRequestHeader('Accept', 'application/json');
    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(1);
        resolve(data as T);
        return;
      }
      const err = toApiError(xhr.status, data);
      notifyUnauthenticated(err);
      reject(err);
    };
    xhr.onerror = () => reject(new ApiError(0, NETWORK_ERROR, ''));
    xhr.onabort = () => reject(new DOMException('aborted', 'AbortError'));
    signal?.addEventListener('abort', () => xhr.abort(), { once: true });
    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });
}
