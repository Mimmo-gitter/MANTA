"use client";

import React, { createContext, useContext, useState } from "react";

export type ShellViewState = "default" | "empty" | "error" | "loading";
export type ShellState = ShellViewState;

interface ShellStateContextValue {
  shellState: ShellViewState;
  setShellState: (state: ShellViewState) => void;
}

const ShellStateContext = createContext<ShellStateContextValue>({
  shellState: "default",
  setShellState: () => {},
});

export function ShellStateProvider({
  children,
  initialState = "default",
}: {
  children: React.ReactNode;
  initialState?: ShellViewState;
}) {
  const [shellState, setShellState] = useState<ShellViewState>(initialState);

  return (
    <ShellStateContext.Provider value={{ shellState, setShellState }}>
      {children}
    </ShellStateContext.Provider>
  );
}

export function useShellState() {
  return useContext(ShellStateContext);
}
