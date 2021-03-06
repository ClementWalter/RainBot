import os

from confluent_kafka import Producer

topic_prefix = os.environ['CLOUDKARAFKA_TOPIC_PREFIX']
conf = {
    'bootstrap.servers': os.environ['CLOUDKARAFKA_BROKERS'],
    'group.id': f'{os.environ["CLOUDKARAFKA_USERNAME"]}-consumer',
    'session.timeout.ms': 6000,
    'default.topic.config': {'auto.offset.reset': 'smallest'},
    'security.protocol': 'SASL_SSL',
    'sasl.mechanisms': 'SCRAM-SHA-256',
    'sasl.username': os.environ['CLOUDKARAFKA_USERNAME'],
    'sasl.password': os.environ['CLOUDKARAFKA_PASSWORD']
}

p = Producer(**conf)
