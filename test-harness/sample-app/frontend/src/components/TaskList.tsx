import type { Task } from "../types";

type TaskListProps = {
  tasks: Task[];
  onDelete: (taskId: number) => Promise<void>;
  onToggleComplete: (task: Task) => Promise<void>;
};

export function TaskList({ tasks, onDelete, onToggleComplete }: TaskListProps): JSX.Element {
  if (tasks.length === 0) {
    return <p>No tasks yet.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Title</th>
          <th>Description</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.id}>
            <td>{task.title}</td>
            <td>{task.description ?? "-"}</td>
            <td>{task.completed ? "Completed" : "Open"}</td>
            <td>
              <button onClick={() => void onToggleComplete(task)}>
                {task.completed ? "Mark Open" : "Mark Done"}
              </button>
              <button onClick={() => void onDelete(task.id)}>Delete</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

