from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import Task
from app.schemas import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)) -> TaskRead:
    task = Task(title=payload.title, description=payload.description, completed=payload.completed)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskRead.model_validate(task)


@router.get("", response_model=list[TaskRead])
async def list_tasks(db: AsyncSession = Depends(get_db)) -> list[TaskRead]:
    result = await db.execute(select(Task).order_by(Task.id.asc()))
    tasks = list(result.scalars().all())
    return [TaskRead.model_validate(task) for task in tasks]


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)) -> TaskRead:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskRead.model_validate(task)


@router.put("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: AsyncSession = Depends(get_db),
) -> TaskRead:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if payload.title is not None:
        task.title = payload.title
    if payload.description is not None:
        task.description = payload.description
    if payload.completed is not None:
        task.completed = payload.completed

    await db.commit()
    await db.refresh(task)
    return TaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)) -> None:
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    await db.delete(task)
    await db.commit()
