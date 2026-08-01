import json
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TaskState, TextPart
from a2a.utils import new_agent_text_message, new_task

from .. import fhir_grounding

logger = logging.getLogger(__name__)


class CatalystAgentExecutor(AgentExecutor):
    """Answers the FHIR sidecar POC's canonical lab questions (feature 011).

    Replaces the earlier M0.0 NL-to-SQL flow (generate_sql against a mocked
    schema) with FHIR-grounded question answering against OE2's embedded FHIR
    provider — see ../fhir_grounding.py. The response is JSON-serialized (the
    full sidecar_response.schema.json shape) into the A2A text artifact;
    catalyst-gateway's a2a_client.py parses it back out.
    """

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        task = context.current_task or new_task(context.message)
        task_updater = TaskUpdater(event_queue, task.id, task.context_id)

        await task_updater.update_status(
            TaskState.working,
            new_agent_text_message(
                "Resolving patient and fetching FHIR resources.",
                task.context_id,
                task.id,
            ),
        )

        response = await fhir_grounding.answer_question(query)
        response_text = json.dumps(response)

        await task_updater.add_artifact(
            [Part(root=TextPart(text=response_text))],
            name="sidecar_response",
        )
        await task_updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if not task:
            return
        task_updater = TaskUpdater(event_queue, task.id, task.context_id)
        await task_updater.update_status(
            TaskState.cancelled,
            new_agent_text_message(
                "Catalyst execution cancelled.",
                task.context_id,
                task.id,
            ),
        )
