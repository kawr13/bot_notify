import threading


print_lock = threading.Lock()

class CustomThread(threading.Thread):
    def __init__(self, group=None, target=None, name=None, args=(), kwargs={}, *, Verbose=None):
        threading.Thread.__init__(self, group, target, name, args, kwargs)
        self._return = None

    def run(self):
        if self._target is not None:
            self._return = self._target(*self._args, **self._kwargs)

    def join(self):
        threading.Thread.join(self)
        return self._return


def thread(func):
    def wrapper(*args, **kwargs):
        my_thread = CustomThread(target=func, args=args, kwargs=kwargs)
        my_thread.start()
        return my_thread

    return wrapper
