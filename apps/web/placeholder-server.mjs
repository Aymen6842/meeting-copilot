// Temporary stand-in for the Next.js app (milestone 1, task 10) so the compose stack has a
// healthy web service. Delete this file and its Dockerfile CMD when the real app lands.
import { createServer } from "node:http";

const port = Number(process.env.PORT ?? 3000);

const server = createServer((request, response) => {
  response.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
  response.end("Meeting Copilot web placeholder\n");
});

server.listen(port, "0.0.0.0", () => {
  console.log(`web placeholder listening on ${port}`);
});

for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
