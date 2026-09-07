"""Explicit v2 verification host. Importing this module starts nothing."""

import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration', required=True, help='Explicit absolute runtime JSON file')
    parser.add_argument('--bind', default='127.0.0.1:8501', help='Existing chosen bind or Unix socket; default loopback:8501')
    parser.add_argument('--integrated', action='store_true', help='Use v2 as primary and retain legacy archive routes')
    args = parser.parse_args(argv)
    from gunicorn.app.base import BaseApplication
    from .bootstrap import from_file
    from .web.app import create_app

    class Host(BaseApplication):
        def load_config(self):
            self.cfg.set('bind', args.bind)
            self.cfg.set('workers', 1)
            self.cfg.set('threads', 4)
            self.cfg.set('preload_app', False)
            self.cfg.set('timeout', 60)
            self.cfg.set('graceful_timeout', 60)
            self.cfg.set('accesslog', None)
            self.cfg.set('post_worker_init', lambda worker: worker.wsgi.extensions['twitchbot.container'].runtime.start())
            def stop(server, worker):
                app = getattr(worker, 'wsgi', None)
                if app is not None:
                    app.extensions['twitchbot.container'].runtime.stop()
            self.cfg.set('worker_exit', stop)

        def load(self):
            if args.integrated:
                # Legacy route modules use the repository's src package namespace.
                # Compose their services from that same namespace so typed errors
                # are handled consistently instead of becoming HTTP 500 responses.
                from src.twitchbot.bootstrap import from_file as integrated_container
                from services.v2_host import create_operational_app
                return create_operational_app(integrated_container(args.configuration))
            return create_app(from_file(args.configuration))

    Host().run()


if __name__ == '__main__':
    main()
