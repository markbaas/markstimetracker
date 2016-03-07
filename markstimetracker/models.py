import datetime
import random
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from sqlalchemy import Column, ForeignKey, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

Base = declarative_base()

today = datetime.date.today()
PERIODS = {'month': (today.replace(day=1),
                     today.replace(day=1) + relativedelta(months=1) - relativedelta(days=1)),
           'last_month': (today.replace(day=1) - relativedelta(months=1),
                          today.replace(day=1, month=today.month) - relativedelta(days=1)),
           'week': (today - relativedelta(days=today.weekday()),
                    today + relativedelta(days=6 - today.weekday())),
           'last_and_current_week': (today - relativedelta(days=today.weekday() + 7),
                                     today + relativedelta(days=6 - today.weekday())),
           }


class Task(Base):
    __tablename__ = 'Tasks'

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer)
    parent = Column(Integer)
    name = Column(String)
    active = Column(Boolean)

    def __new__(cls, *args, **kwargs):
        for key, args in PERIODS.items():
            rel = relationship(
                'Event',
                primaryjoin='and_(Task.task_id==Event.task_id, '
                            'Event.start >= "{}", Event.start <= "{}")'.format(*args),
                order_by='Event.start'
            )
            if not hasattr(cls, 'events_' + key):
                setattr(cls, 'events_' + key, rel)

        return super(Task, cls).__new__(cls)

    @classmethod
    def get_or_create(cls, session, name, task_id=None, parent=None):
        if not task_id:
            task_id = random.randint(1000000, 9999999)

        task = session.query(cls).filter(cls.name == name).first()
        if not task:
            obj = Task(name=name, task_id=task_id, parent=parent)
            session.add(obj)
            return obj
        else:
            return task

    def get_spent_time_per_day(self, period='week'):
        d = defaultdict(float)
        for event in getattr(self, 'events_' + period):
            d[event.start_date.date()] += event.spent_time / 3600.

        for key, raw_time in d.items():
            d[key] = round(raw_time * 4) / 4  # rounded on quarter hours

        return d

    @hybrid_property
    def description(self):
        return '{} - {}'.format(self.task_id, self.name)


class Event(Base):
    __tablename__ = 'Events'

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('Tasks.task_id'))
    task = relationship(Task, backref='events')
    comment = Column(String)
    start = Column(String)
    end = Column(String)

    @hybrid_property
    def start_date(self):
        return datetime.datetime.strptime(self.start, '%Y-%m-%d %H:%M:%S.%f')

    @hybrid_property
    def end_date(self):
        return datetime.datetime.strptime(self.end, '%Y-%m-%d %H:%M:%S.%f') if self.end else None

    @hybrid_property
    def spent_time(self):
        if self.end_date is None:
            return (datetime.datetime.now() - self.start_date).seconds
        else:
            return (self.end_date - self.start_date).seconds

    @classmethod
    def get_spent_time_period(cls, session, start, end):
        events = session.query(cls).filter(cls.start.between(start.strftime("%Y-%m-%d %H:%M:%S"),
                                                             end.strftime("%Y-%m-%d %H:%M:%S")))
        return sum([round(x.spent_time / 3600. * 4) / 4 for x in events])


def init_db(engine):
    Base.metadata.create_all(engine)
