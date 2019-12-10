from .maas_request import MaaSRequest
from .message import Message, MessageEventType, Response
from typing import Optional


class SchedulerRequestMessage(Message):

    event_type: MessageEventType = MessageEventType.SCHEDULER_REQUEST
    """ :class:`MessageEventType`: the event type for this message implementation """

    @classmethod
    def factory_init_from_deserialized_json(cls, json_obj: dict):
        """
        Factory create a new instance of this type based on a JSON object dictionary deserialized from received JSON.

        Parameters
        ----------
        json_obj

        Returns
        -------
        A new object of this type instantiated from the deserialize JSON object dictionary, or none if the provided
        parameter could not be used to instantiated a new object.
        """
        try:
            model_request = MaaSRequest.factory_init_correct_subtype_from_deserialized_json(json_obj['model_request'])
            if model_request is not None:
                return cls(model_request=model_request, user_id=json_obj['user_id'], cpus=json_obj['cpus'],
                           mem=json_obj['mem'])
            else:
                return None
        except:
            return None

    def __init__(self, model_request: MaaSRequest, user_id: str, cpus: Optional[int] = None, mem: Optional[int] = None):
        self.model_request = model_request
        self.user_id = user_id
        # TODO come up with better way of determining this for the running system; for now, ensure a value is set
        if cpus is None:
            self.cpus_unset = True
            self.cpus = 4
        else:
            self.cpus_unset = False
            self.cpus = cpus
        if mem is None:
            self.memory_unset = True
            self.memory = 500000
        else:
            self.memory_unset = False
            self.memory = mem

    def to_dict(self) -> dict:
        return {'model_request': self.model_request.to_dict(), 'user_id': self.user_id, 'cpus': self.cpus,
                'mem': self.memory}


class SchedulerRequestResponse(Response):
    response_to_type = SchedulerRequestMessage

    @property
    def job_id(self):
        if self.success:
            return self.data['job_id']
        else:
            return -1
