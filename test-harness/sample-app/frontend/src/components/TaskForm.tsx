import { FormEvent, useState } from "react";
import type { Task, TaskCreate } from "../types";

type TaskFormProps = {
  onSubmit: (payload: TaskCreate) => Promise<void>;
  editingTask?: Task | null;
};

export function TaskForm({ onSubmit, editingTask }: TaskFormProps): JSX.Element {
  const [title, setTitle] = useState(editingTask?.title ?? "");
  const [description, setDescription] = useState(editingTask?.description ?? "");

  async function handleSubmit(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (!title.trim()) {
      return;
    }
    await onSubmit({ title: title.trim(), description });
    setTitle("");
    setDescription("");
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2>{editingTask ? "Edit Task" : "Create Task"}</h2>
      <label>
        Title
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Task title"
        />
      </label>
      <label>
        Description
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Task description"
        />
      </label>
      <button type="submit">{editingTask ? "Save Changes" : "Create Task"}</button>
    </form>
  );
}

