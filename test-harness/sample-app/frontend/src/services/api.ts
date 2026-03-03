import axios from "axios";
import type { Task, TaskCreate, TaskUpdate } from "../types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8001/api/v1";

const client = axios.create({
  baseURL: API_BASE,
  timeout: 10000
});

export async function listTasks(): Promise<Task[]> {
  const response = await client.get<Task[]>("/tasks");
  return response.data;
}

export async function createTask(payload: TaskCreate): Promise<Task> {
  const response = await client.post<Task>("/tasks", payload);
  return response.data;
}

export async function updateTask(taskId: number, payload: TaskUpdate): Promise<Task> {
  const response = await client.put<Task>(`/tasks/${taskId}`, payload);
  return response.data;
}

export async function deleteTask(taskId: number): Promise<void> {
  await client.delete(`/tasks/${taskId}`);
}

