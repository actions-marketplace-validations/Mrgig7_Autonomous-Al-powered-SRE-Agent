import { useEffect, useState } from "react";
import { TaskForm } from "./components/TaskForm";
import { TaskList } from "./components/TaskList";
import { createTask, deleteTask, listTasks, updateTask } from "./services/api";
import type { Task, TaskCreate } from "./types";

export default function App(): JSX.Element {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    try {
      const next = await listTasks();
      setTasks(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load tasks");
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleCreate(payload: TaskCreate): Promise<void> {
    await createTask(payload);
    await refresh();
  }

  async function handleDelete(taskId: number): Promise<void> {
    await deleteTask(taskId);
    await refresh();
  }

  async function handleToggleComplete(task: Task): Promise<void> {
    await updateTask(task.id, { completed: !task.completed });
    await refresh();
  }

  return (
    <main>
      <h1>Task Manager Platform</h1>
      {error ? <p role="alert">{error}</p> : null}
      <TaskForm onSubmit={handleCreate} />
      <TaskList tasks={tasks} onDelete={handleDelete} onToggleComplete={handleToggleComplete} />
    </main>
  );
}

