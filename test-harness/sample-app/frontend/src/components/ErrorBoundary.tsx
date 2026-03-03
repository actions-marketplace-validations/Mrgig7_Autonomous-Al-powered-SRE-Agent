import React from "react";

type Props = {
  children: React.ReactNode;
};

type State = {
  hasError: boolean;
};

export class ErrorBoundary extends React.Component<Props, State> {
  public state: State = { hasError: false };

  public static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  public componentDidCatch(error: unknown): void {
    // eslint-disable-next-line no-console
    console.error("Task Manager UI crashed", error);
  }

  public render(): React.ReactNode {
    if (this.state.hasError) {
      return <p>Something went wrong in the dashboard.</p>;
    }
    return this.props.children;
  }
}

