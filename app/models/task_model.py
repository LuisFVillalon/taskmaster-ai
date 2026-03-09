from sqlalchemy import Column, Integer, String, Boolean, DateTime, Time, Numeric, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database.database import Base
from app.models.task_tag_model import task_tags

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    category = Column(String, nullable=True)
    completed = Column(Boolean, default=False)
    urgent = Column(Boolean, default=False)
    due_date = Column(DateTime, nullable=True)
    due_time = Column(Time, nullable=True)
    created_date = Column(DateTime, server_default=func.now(), nullable=False)
    completed_date = Column(DateTime, nullable=True)
    estimated_time = Column(Numeric(precision=10, scale=2), nullable=True)
    complexity = Column(Integer, nullable=True)
    parent_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    user_id = Column(Integer, nullable=True)
    tags = relationship(
        "Tag",
        secondary=task_tags,
        back_populates="tasks"
    )
    subtasks = relationship(
        "Task",
        remote_side=[id],
        backref="parent_task"
    )
