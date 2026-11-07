from contextlib import contextmanager


@contextmanager
def instance_lock(root):
    with (root/'relay.lock').open('a+b') as handle:
        if __import__('os').name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1): handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally:
            if __import__('os').name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle, fcntl.LOCK_UN)
