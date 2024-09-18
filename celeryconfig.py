from celery import Celery
import os

def make_celery(app):
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

    celery = Celery(
        app.import_name,
        backend=redis_url,
        broker=redis_url
    )

    celery.conf.update(app.config)
    return celery