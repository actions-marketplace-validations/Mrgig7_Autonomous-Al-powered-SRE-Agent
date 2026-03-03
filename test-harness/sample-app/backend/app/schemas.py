from datetime import datetime

from pydantic import BaseModel, Field


class TaskBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2048)


class TaskCreate(TaskBase):
    completed: bool = False


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2048)
    completed: bool | None = None


class TaskRead(TaskBase):
    id: int
    completed: bool
    created_at: datetime
    updated_at: datetime | None = None

    class Config:
        from_attributes = True
