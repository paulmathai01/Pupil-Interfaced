'''
(*)~---------------------------------------------------------------------------
Pupil - eye tracking platform
Copyright (C) 2012-2018 Pupil Labs

Distributed under the terms of the GNU
Lesser General Public License (LGPL v3.0).
See COPYING and COPYING.LESSER for license details.
---------------------------------------------------------------------------~(*)
'''

from ctypes import c_bool
import multiprocessing as mp
# mp = mp.get_context('fork')
import logging
logger = logging.getLogger(__name__)


class EarlyCancellationError(Exception):
    pass


class Task_Proxy(object):
    '''Future like object that runs a given generator in the background and returns is able to return the results incrementally'''
    def __init__(self, name, generator, args=(), kwargs={}):
        super().__init__()

        self._should_terminate_flag = mp.Value(c_bool, 0)
        self._completed = False
        self._canceled = False

        pipe_recv, pipe_send = mp.Pipe(False)
        wrapper_args = [pipe_send, self._should_terminate_flag, generator]
        wrapper_args.extend(args)
        self.process = mp.Process(target=self._wrapper, name=name, args=wrapper_args, kwargs=kwargs)
        self.process.daemon = True
        self.process.start()
        self.pipe = pipe_recv

    def _wrapper(self, pipe, _should_terminate_flag, generator, *args, **kwargs):
        '''Executed in background, pipes generator results to foreground'''
        logger.debug('Entering _wrapper')
        try:
            for datum in generator(*args, **kwargs):
                if _should_terminate_flag.value:
                    raise EarlyCancellationError('Task was cancelled')
                pipe.send(datum)
        except Exception as e:
            pipe.send(e)
            if not isinstance(e, EarlyCancellationError):
                import traceback
                print(traceback.format_exc())
        else:
            pipe.send(StopIteration())
        finally:
            pipe.close()
            logger.debug('Exiting _wrapper')

    def fetch(self):
        '''Fetches progress and available results from background'''
        if self.completed or self.canceled:
            return

        while self.pipe.poll(0):
            try:
                datum = self.pipe.recv()
            except EOFError:
                logger.debug("Process canceled be user.")
                self._canceled = True
                return
            else:
                if isinstance(datum, StopIteration):
                    self._completed = True
                    return
                elif isinstance(datum, EarlyCancellationError):
                    self._canceled = True
                    return
                elif isinstance(datum, Exception):
                    raise datum
                else:
                    yield datum

    def cancel(self, timeout=1):
        if not (self.completed or self.canceled):
            self._should_terminate_flag.value = True
            for x in self.fetch():
                # fetch to flush pipe to allow process to react to cancel comand.
                pass
        if self.process is not None:
            self.process.join(timeout)
            self.process = None

    @property
    def completed(self):
        return self._completed

    @property
    def canceled(self):
        return self._canceled

    def __del__(self):
        self.cancel(timeout=.1)
        self.process = None


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(processName)s - [%(levelname)s] %(name)s: %(message)s')

    def example_generator(mu=0., sigma=1., steps=100):
        '''samples `N(\mu, \sigma^2)`'''
        import numpy as np
        from time import sleep
        for i in range(steps):
            # yield progress, datum
            yield (i + 1) / steps, sigma * np.random.randn() + mu
            sleep(np.random.rand() * .1)

    # initialize task proxy
    task = Task_Proxy('Background', example_generator, args=(5., 3.), kwargs={'steps': 100})

    from time import time, sleep
    start = time()
    maximal_duration = 2.
    while time() - start < maximal_duration:
        # fetch all available results
        for progress, random_number in task.fetch():
            logger.debug('[{:3.0f}%] {:0.2f}'.format(progress * 100, random_number))

        # test if task is completed
        if task.completed:
            break
        sleep(1.)

    logger.debug('Canceling task')
    task.cancel(timeout=1)
    logger.debug('Task done')
