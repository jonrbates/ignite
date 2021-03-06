import os
import tempfile
import warnings
from datetime import datetime
from typing import Mapping, Optional

import torch

from ignite.contrib.handlers.base_logger import (
    BaseLogger,
    BaseOptimizerParamsHandler,
    BaseOutputHandler,
    BaseWeightsHistHandler,
    BaseWeightsScalarHandler,
    global_step_from_engine,
)
from ignite.handlers.checkpoint import DiskSaver

__all__ = [
    "TrainsLogger",
    "TrainsSaver",
    "OptimizerParamsHandler",
    "OutputHandler",
    "WeightsScalarHandler",
    "WeightsHistHandler",
    "GradsScalarHandler",
    "GradsHistHandler",
    "global_step_from_engine",
]


class OutputHandler(BaseOutputHandler):
    """Helper handler to log engine's output and/or metrics

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the evaluator on the validation dataset and log NLL, Accuracy metrics after
            # each epoch. We setup `global_step_transform=global_step_from_engine(trainer)` to take the epoch
            # of the `trainer`:
            trains_logger.attach(evaluator,
                             log_handler=OutputHandler(tag="validation",
                                                       metric_names=["nll", "accuracy"],
                                                       global_step_transform=global_step_from_engine(trainer)),
                             event_name=Events.EPOCH_COMPLETED)

        Another example, where model is evaluated every 500 iterations:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            @trainer.on(Events.ITERATION_COMPLETED(every=500))
            def evaluate(engine):
                evaluator.run(validation_set, max_epochs=1)

            # Create a logger

            trains_logger = TrainsLogger()

            def global_step_transform(*args, **kwargs):
                return trainer.state.iteration

            # Attach the logger to the evaluator on the validation dataset and log NLL, Accuracy metrics after
            # every 500 iterations. Since evaluator engine does not have access to the training iteration, we
            # provide a global_step_transform to return the trainer.state.iteration for the global_step, each time
            # evaluator metrics are plotted on Trains.

            trains_logger.attach(evaluator,
                             log_handler=OutputHandler(tag="validation",
                                                       metrics=["nll", "accuracy"],
                                                       global_step_transform=global_step_transform),
                             event_name=Events.EPOCH_COMPLETED)

    Args:
        tag (str): common title for all produced plots. For example, "training"
        metric_names (list of str, optional): list of metric names to plot or a string "all" to plot all available
            metrics.
        output_transform (callable, optional): output transform function to prepare `engine.state.output` as a number.
            For example, `output_transform = lambda output: output`
            This function can also return a dictionary, e.g `{"loss": loss1, "another_loss": loss2}` to label the plot
            with corresponding keys.
        another_engine (Engine): Deprecated (see :attr:`global_step_transform`). Another engine to use to provide the
            value of event. Typically, user can provide
            the trainer if this handler is attached to an evaluator and thus it logs proper trainer's
            epoch/iteration value.
        global_step_transform (callable, optional): global step transform function to output a desired global step.
            Input of the function is `(engine, event_name)`. Output of function should be an integer.
            Default is None, global_step based on attached engine. If provided,
            uses function output as global_step. To setup global step from another engine, please use
            :meth:`~ignite.contrib.handlers.trains_logger.global_step_from_engine`.

    Note:

        Example of `global_step_transform`:

        .. code-block:: python

            def global_step_transform(engine, event_name):
                return engine.state.get_event_attrib_value(event_name)

    """

    def __init__(self, tag, metric_names=None, output_transform=None, another_engine=None, global_step_transform=None):
        super(OutputHandler, self).__init__(tag, metric_names, output_transform, another_engine, global_step_transform)

    def __call__(self, engine, logger, event_name):

        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler OutputHandler works only with TrainsLogger")

        metrics = self._setup_output_metrics(engine)

        global_step = self.global_step_transform(engine, event_name)

        if not isinstance(global_step, int):
            raise TypeError(
                "global_step must be int, got {}."
                " Please check the output of global_step_transform.".format(type(global_step))
            )

        for key, value in metrics.items():
            if isinstance(value, (float, int)):
                logger.trains_logger.report_scalar(title=self.tag, series=key, iteration=global_step, value=value)
            else:
                warnings.warn("TrainsLogger output_handler can not log " "metrics value type {}".format(type(value)))


class OptimizerParamsHandler(BaseOptimizerParamsHandler):
    """Helper handler to log optimizer parameters

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log optimizer's parameters, e.g. learning rate at each iteration
            trains_logger.attach(trainer,
                                 log_handler=OptimizerParamsHandler(optimizer),
                                 event_name=Events.ITERATION_STARTED)

    Args:
        optimizer (torch.optim.Optimizer): torch optimizer which parameters to log
        param_name (str): parameter name
        tag (str, optional): common title for all produced plots. For example, generator
    """

    def __init__(self, optimizer, param_name="lr", tag=None):
        super(OptimizerParamsHandler, self).__init__(optimizer, param_name, tag)

    def __call__(self, engine, logger, event_name):
        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler OptimizerParamsHandler works only with TrainsLogger")

        global_step = engine.state.get_event_attrib_value(event_name)
        tag_prefix = "{}/".format(self.tag) if self.tag else ""
        params = {
            str(i): float(param_group[self.param_name]) for i, param_group in enumerate(self.optimizer.param_groups)
        }

        for k, v in params.items():
            logger.trains_logger.report_scalar(
                title="{}{}".format(tag_prefix, self.param_name), series=k, value=v, iteration=global_step
            )


class WeightsScalarHandler(BaseWeightsScalarHandler):
    """Helper handler to log model's weights as scalars.
    Handler iterates over named parameters of the model, applies reduction function to each parameter
    produce a scalar and then logs the scalar.

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log model's weights norm after each iteration
            trains_logger.attach(trainer,
                             log_handler=WeightsScalarHandler(model, reduction=torch.norm),
                             event_name=Events.ITERATION_COMPLETED)

    Args:
        model (torch.nn.Module): model to log weights
        reduction (callable): function to reduce parameters into scalar
        tag (str, optional): common title for all produced plots. For example, generator

    """

    def __init__(self, model, reduction=torch.norm, tag=None):
        super(WeightsScalarHandler, self).__init__(model, reduction, tag=tag)

    def __call__(self, engine, logger, event_name):

        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler WeightsScalarHandler works only with TrainsLogger")

        global_step = engine.state.get_event_attrib_value(event_name)
        tag_prefix = "{}/".format(self.tag) if self.tag else ""
        for name, p in self.model.named_parameters():
            if p.grad is None:
                continue

            title_name, _, series_name = name.partition(".")
            logger.trains_logger.report_scalar(
                title="{}weights_{}/{}".format(tag_prefix, self.reduction.__name__, title_name),
                series=series_name,
                value=self.reduction(p.data),
                iteration=global_step,
            )


class WeightsHistHandler(BaseWeightsHistHandler):
    """Helper handler to log model's weights as histograms.

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log model's weights norm after each iteration
            trains_logger.attach(trainer,
                                 log_handler=WeightsHistHandler(model),
                                 event_name=Events.ITERATION_COMPLETED)

    Args:
        model (torch.nn.Module): model to log weights
        tag (str, optional): common title for all produced plots. For example, 'generator'

    """

    def __init__(self, model, tag=None):
        super(WeightsHistHandler, self).__init__(model, tag=tag)

    def __call__(self, engine, logger, event_name):
        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler 'WeightsHistHandler' works only with TrainsLogger")

        global_step = engine.state.get_event_attrib_value(event_name)
        tag_prefix = "{}/".format(self.tag) if self.tag else ""
        for name, p in self.model.named_parameters():
            if p.grad is None:
                continue

            title_name, _, series_name = name.partition(".")

            logger.grad_helper.add_histogram(
                title="{}weights_{}".format(tag_prefix, title_name),
                series=series_name,
                step=global_step,
                hist_data=p.grad.detach().cpu().numpy(),
            )


class GradsScalarHandler(BaseWeightsScalarHandler):
    """Helper handler to log model's gradients as scalars.
    Handler iterates over the gradients of named parameters of the model, applies reduction function to each parameter
    produce a scalar and then logs the scalar.

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log model's weights norm after each iteration
            trains_logger.attach(trainer,
                                 log_handler=GradsScalarHandler(model, reduction=torch.norm),
                                 event_name=Events.ITERATION_COMPLETED)

    Args:
        model (torch.nn.Module): model to log weights
        reduction (callable): function to reduce parameters into scalar
        tag (str, optional): common title for all produced plots. For example, generator

    """

    def __init__(self, model, reduction=torch.norm, tag=None):
        super(GradsScalarHandler, self).__init__(model, reduction, tag=tag)

    def __call__(self, engine, logger, event_name):
        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler GradsScalarHandler works only with TrainsLogger")

        global_step = engine.state.get_event_attrib_value(event_name)
        tag_prefix = "{}/".format(self.tag) if self.tag else ""
        for name, p in self.model.named_parameters():
            if p.grad is None:
                continue

            title_name, _, series_name = name.partition(".")
            logger.trains_logger.report_scalar(
                title="{}grads_{}/{}".format(tag_prefix, self.reduction.__name__, title_name),
                series=series_name,
                value=self.reduction(p.data),
                iteration=global_step,
            )


class GradsHistHandler(BaseWeightsHistHandler):
    """Helper handler to log model's gradients as histograms.

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log model's weights norm after each iteration
            trains_logger.attach(trainer,
                                 log_handler=GradsHistHandler(model),
                                 event_name=Events.ITERATION_COMPLETED)

    Args:
        model (torch.nn.Module): model to log weights
        tag (str, optional): common title for all produced plots. For example, 'generator'

    """

    def __init__(self, model, tag=None):
        super(GradsHistHandler, self).__init__(model, tag=tag)

    def __call__(self, engine, logger, event_name):
        if not isinstance(logger, TrainsLogger):
            raise RuntimeError("Handler 'GradsHistHandler' works only with TrainsLogger")

        global_step = engine.state.get_event_attrib_value(event_name)
        tag_prefix = "{}/".format(self.tag) if self.tag else ""
        for name, p in self.model.named_parameters():
            if p.grad is None:
                continue

            title_name, _, series_name = name.partition(".")

            logger.grad_helper.add_histogram(
                title="{}grads_{}".format(tag_prefix, title_name),
                series=series_name,
                step=global_step,
                hist_data=p.grad.detach().cpu().numpy(),
            )


class TrainsLogger(BaseLogger):
    """
    `Trains <https://github.com/allegroai/trains>`_ handler to log metrics, text, model/optimizer parameters,
    plots during training and validation.
    Also supports model checkpoints logging and upload to the storage solution of your choice (i.e. Trains File server,
     S3 bucket etc.)

    .. code-block:: bash

        pip install trains
        trains-init

    Args:
        project_name (str): The name of the project in which the experiment will be created. If the project
            does not exist, it is created. If ``project_name`` is ``None``, the repository name is used. (Optional)
        task_name (str): The name of Task (experiment). If ``task_name`` is ``None``, the Python experiment
            script's file name is used. (Optional)
        task_type (str): Optional. The task type. Valid values are:
            - ``TaskTypes.training`` (Default)
            - ``TaskTypes.train``
            - ``TaskTypes.testing``
            - ``TaskTypes.inference``
        report_freq (int): Optional. Histogram processing frequency (handle hist values every X calls to the handler).
           Affects ``GradsHistHandler`` and ``WeightsHistHandler``. Default value is 100.
        histogram_update_freq_multiplier (int): Optional. Histogram report frequency (report first X histograms and
           once every X reports afterwards). Default value is 10.
        histogram_granularity (int): Optional. Histogram sampling granularity. Default is 50.

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *

            # Create a logger

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            # Attach the logger to the trainer to log training loss at each iteration
            trains_logger.attach(trainer,
                                 log_handler=OutputHandler(tag="training",
                                 output_transform=lambda loss: {"loss": loss}),
                                 event_name=Events.ITERATION_COMPLETED)

            # Attach the logger to the evaluator on the training dataset and log NLL, Accuracy metrics after each epoch
            # We setup `global_step_transform=global_step_from_engine(trainer)` to take the epoch
            # of the `trainer` instead of `train_evaluator`.
            trains_logger.attach(train_evaluator,
                                 log_handler=OutputHandler(tag="training",
                                                           metric_names=["nll", "accuracy"],
                                                           global_step_transform=global_step_from_engine(trainer)),
                                event_name=Events.EPOCH_COMPLETED)

            # Attach the logger to the evaluator on the validation dataset and log NLL, Accuracy metrics after
            # each epoch. We setup `global_step_transform=global_step_from_engine(trainer)` to take the epoch of the
            # `trainer` instead of `evaluator`.
            trains_logger.attach(evaluator,
                                 log_handler=OutputHandler(tag="validation",
                                                          metric_names=["nll", "accuracy"],
                                                          global_step_transform=global_step_from_engine(trainer)),
                                 event_name=Events.EPOCH_COMPLETED)

            # Attach the logger to the trainer to log optimizer's parameters, e.g. learning rate at each iteration
            trains_logger.attach(trainer,
                                 log_handler=OptimizerParamsHandler(optimizer),
                                 event_name=Events.ITERATION_STARTED)

            # Attach the logger to the trainer to log model's weights norm after each iteration
            trains_logger.attach(trainer,
                                 log_handler=WeightsScalarHandler(model),
                                 event_name=Events.ITERATION_COMPLETED)


    """

    def __init__(self, *_, **kwargs):
        try:
            import trains
        except ImportError:
            raise RuntimeError(
                "This contrib module requires trains to be installed. "
                "You may install trains using: \n pip install trains \n"
            )

        experiment_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k
            not in (
                "project_name",
                "task_name",
                "task_type",
                "report_freq",
                "histogram_update_freq_multiplier",
                "histogram_granularity",
            )
        }

        if self.bypass_mode():
            warnings.warn("TrainsSaver: running in bypass mode")

            class _Stub(object):
                def __call__(self, *_, **__):
                    return self

                def __getattr__(self, attr):
                    if attr in ("name", "id"):
                        return ""
                    return self

                def __setattr__(self, attr, val):
                    pass

            self._task = _Stub()
        else:
            self._task = trains.Task.init(
                project_name=kwargs.get("project_name"),
                task_name=kwargs.get("task_name"),
                task_type=kwargs.get("task_type", trains.Task.TaskTypes.training),
                **experiment_kwargs,
            )

        self.trains_logger = self._task.get_logger()

        self.grad_helper = trains.binding.frameworks.tensorflow_bind.WeightsGradientHistHelper(
            logger=self.trains_logger,
            report_freq=kwargs.get("report_freq", 100),
            histogram_update_freq_multiplier=kwargs.get("histogram_update_freq_multiplier", 10),
            histogram_granularity=kwargs.get("histogram_granularity", 50),
        )

    @classmethod
    def set_bypass_mode(cls, bypass: bool) -> None:
        """
        Will bypass all outside communication, and will drop all logs.
        Should only be used in "standalone mode", when there is no access to the *trains-server*.
        Args:
            bypass: If ``True``, all outside communication is skipped.
        """
        setattr(cls, "_bypass", bypass)

    @classmethod
    def bypass_mode(cls) -> bool:
        """
        Returns the bypass mode state.
        Note:
            `GITHUB_ACTIONS` env will automatically set bypass_mode to ``True``
            unless overridden specifically with ``TrainsLogger.set_bypass_mode(False)``.
        Return:
            If True, all outside communication is skipped.
        """
        return getattr(cls, "_bypass", bool(os.environ.get("CI")))

    def close(self):
        self.trains_logger.flush()

    def _create_output_handler(self, *args, **kwargs):
        return OutputHandler(*args, **kwargs)

    def _create_opt_params_handler(self, *args, **kwargs):
        return OptimizerParamsHandler(*args, **kwargs)


class TrainsSaver(DiskSaver):
    """Handler that saves input checkpoint as Trains artifacts

    Examples:

        .. code-block:: python

            from ignite.contrib.handlers.trains_logger import *
            from ignite.handlers import Checkpoint

            trains_logger = TrainsLogger(project_name="pytorch-ignite-integration",
                                         task_name="cnn-mnist"
                                         )

            to_save = {"model": model}

            handler = Checkpoint(to_save, TrainsSaver(trains_logger), n_saved=1,
                                 score_function=lambda e: 123, score_name="acc",
                                 filename_prefix="best",
                                 global_step_transform=global_step_from_engine(trainer))

            validation_evaluator.add_event_handler(Events.EVENT_COMPLETED, handler)

    """

    def __init__(self, logger: TrainsLogger, output_uri: str = None, dirname: str = None, *args, **kwargs):
        if not isinstance(logger, TrainsLogger):
            raise TypeError("logger must be an instance of TrainsLogger")

        try:
            from trains import Task
        except ImportError:
            raise RuntimeError(
                "This contrib module requires trains to be installed. "
                "You may install trains using: \n pip install trains \n"
            )

        if not dirname:
            dirname = tempfile.mkdtemp(
                prefix="ignite_checkpoints_{}".format(datetime.now().strftime("%Y_%m_%d_%H_%M_%S_"))
            )
            warnings.warn("TrainsSaver created a temporary checkpoints directory: {}".format(dirname))

        super(TrainsSaver, self).__init__(dirname=dirname, *args, **kwargs)

        self.logger = logger
        self.task = Task.current_task()

        if output_uri:
            self.task.output_uri = output_uri

    def __call__(self, checkpoint: Mapping, filename: str) -> None:
        super(TrainsSaver, self).__call__(checkpoint, filename)

        try:
            import trains
        except ImportError:
            raise RuntimeError(
                "This contrib module requires trains to be installed. "
                "You may install trains using: \n pip install trains \n"
            )

        if self._atomic:
            # If atomic, DiskSaver's implementation first stores checkpoint into a temporary file
            # and prohibits trains to automatically detect correct artifact path and name
            path = os.path.join(self.dirname, filename)
            if os.path.exists(path):
                trains.binding.frameworks.WeightsFileHandler.create_output_model(
                    model=checkpoint,
                    saved_path=path,
                    framework=trains.model.Framework.pytorch,
                    task=self.task,
                    singlefile=True,
                    model_name=os.path.basename(filename),
                )

    def get_local_copy(self, filename: str) -> Optional[str]:
        """
        Get artifact local copy
        :param filename: artifact name.
        :return: a local path to a downloaded copy of the artifact
        """
        artifact = self.task.artifacts.get(filename)
        if artifact:
            return artifact.get_local_copy()
        self.task.get_logger().report_text("Can not find artifact {}".format(filename))
