declare module "@sinclair/typebox" {
  export const Type: any;
}

declare module "@mariozechner/pi-agent-core" {
  export type AgentToolUpdateCallback = (update: { content?: any[]; details?: any; isError?: boolean }) => void;
}

declare module "@mariozechner/pi-ai" {
  export interface MessagePartText {
    type: "text";
    text: string;
  }

  export interface Message {
    role: string;
    content: Array<MessagePartText | { type: string; [key: string]: any }>;
    [key: string]: any;
  }
}

declare module "@mariozechner/pi-coding-agent" {
  export interface ExtensionContext {
    cwd: string;
    hasUI?: boolean;
    ui?: {
      confirm(title: string, body: string): Promise<boolean>;
      setStatus(key: string, value?: string): void;
    };
  }

  export interface RegisteredTool {
    name: string;
    label?: string;
    description?: string;
    promptSnippet?: string;
    promptGuidelines?: string[];
    parameters?: any;
    execute: (
      toolCallId: string,
      params: any,
      signal?: AbortSignal,
      onUpdate?: (update: { content?: any[]; details?: any; isError?: boolean }) => void,
      ctx?: ExtensionContext,
    ) => Promise<any> | any;
  }

  export interface ExtensionAPI {
    on(eventName: string, handler: (event: any, ctx: ExtensionContext) => Promise<any> | any): void;
    registerTool(tool: RegisteredTool): void;
    registerProvider(name: string, config: any): void;
  }

  export function parseFrontmatter<T = Record<string, any>>(content: string): { frontmatter: T; body: string };
  export function getAgentDir(): string;
}

declare const process: any;

declare module "node:path" {
  const pathAny: any;
  export = pathAny;
}

declare module "node:fs" {
  const fsAny: any;
  export = fsAny;
}

declare module "node:os" {
  const osAny: any;
  export = osAny;
}

declare module "node:child_process" {
  export function spawn(...args: any[]): any;
}
