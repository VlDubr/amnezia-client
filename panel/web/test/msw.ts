import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { EMPTY_LOAD } from "./load";

export const server = setupServer(http.get("/api/admin/servers/:id/load", () => HttpResponse.json(EMPTY_LOAD)));
