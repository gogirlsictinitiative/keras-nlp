# Copyright 2024 The KerasNLP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

import keras
from rich import console as rich_console
from rich import markup
from rich import table as rich_table

from keras_nlp.src.api_export import keras_nlp_export
from keras_nlp.src.utils.keras_utils import print_msg
from keras_nlp.src.utils.pipeline_model import PipelineModel
from keras_nlp.src.utils.preset_utils import TASK_CONFIG_FILE
from keras_nlp.src.utils.preset_utils import TASK_WEIGHTS_FILE
from keras_nlp.src.utils.preset_utils import find_subclass
from keras_nlp.src.utils.preset_utils import get_preset_loader
from keras_nlp.src.utils.preset_utils import list_presets
from keras_nlp.src.utils.preset_utils import list_subclasses
from keras_nlp.src.utils.preset_utils import save_serialized_object
from keras_nlp.src.utils.python_utils import classproperty


@keras_nlp_export("keras_nlp.models.Task")
class Task(PipelineModel):
    """Base class for all Task models.

    A `Task` wraps a `keras_nlp.models.Backbone` and
    a `keras_nlp.models.Preprocessor` to create a model that can be directly
    used for training, fine-tuning, and prediction for a given text problem.

    All `Task` models have `backbone` and `preprocessor` properties. By
    default `fit()`, `predict()` and `evaluate()` will preprocess all inputs
    automatically. To preprocess inputs separately or with a custom function,
    you can set `task.preprocessor = None`, which disable any automatic
    preprocessing on inputs.

    All `Task` classes include a `from_preset()` constructor which can be used
    to load a pre-trained config and weights. Calling `from_preset()` on a task
    will automatically instantiate a `keras_nlp.models.Backbone` and
    `keras_nlp.models.Preprocessor`.

    Args:
        compile: boolean, defaults to `True`. If `True` will compile the model
            with default parameters on construction. Model can still be
            recompiled with a new loss, optimizer and metrics before training.
    """

    backbone_cls = None
    preprocessor_cls = None

    def __init__(self, *args, compile=True, **kwargs):
        super().__init__(*args, **kwargs)
        self._functional_layer_ids = set(
            id(layer) for layer in self._flatten_layers()
        )
        self._initialized = True
        if self.backbone is not None:
            self.dtype_policy = self._backbone.dtype_policy
        if compile:
            # Default compilation.
            self.compile()

    def preprocess_samples(self, x, y=None, sample_weight=None):
        if self.preprocessor is not None:
            return self.preprocessor(x, y=y, sample_weight=sample_weight)
        else:
            return super().preprocess_samples(x, y, sample_weight)

    def __setattr__(self, name, value):
        # Work around setattr issues for Keras 2 and Keras 3 torch backend.
        # Since all our state is covered by functional model we can route
        # around custom setattr calls.
        is_property = isinstance(getattr(type(self), name, None), property)
        is_unitialized = not hasattr(self, "_initialized")
        is_torch = keras.config.backend() == "torch"
        if is_torch and (is_property or is_unitialized):
            return object.__setattr__(self, name, value)
        return super().__setattr__(name, value)

    @property
    def backbone(self):
        """A `keras_nlp.models.Backbone` model with the core architecture."""
        return getattr(self, "_backbone", None)

    @backbone.setter
    def backbone(self, value):
        self._backbone = value

    @property
    def preprocessor(self):
        """A `keras_nlp.models.Preprocessor` layer used to preprocess input."""
        return getattr(self, "_preprocessor", None)

    @preprocessor.setter
    def preprocessor(self, value):
        self._preprocessor = value

    def get_config(self):
        # Don't chain to super here. The default `get_config()` for functional
        # models is nested and cannot be passed to our Task constructors.
        return {
            "backbone": keras.layers.serialize(self.backbone),
            "preprocessor": keras.layers.serialize(self.preprocessor),
            "name": self.name,
        }

    @classmethod
    def from_config(cls, config):
        # The default `from_config()` for functional models will return a
        # vanilla `keras.Model`. We override it to get a subclass instance back.
        if "backbone" in config and isinstance(config["backbone"], dict):
            config["backbone"] = keras.layers.deserialize(config["backbone"])
        if "preprocessor" in config and isinstance(
            config["preprocessor"], dict
        ):
            config["preprocessor"] = keras.layers.deserialize(
                config["preprocessor"]
            )
        return cls(**config)

    @classproperty
    def presets(cls):
        """List built-in presets for a `Task` subclass."""
        presets = list_presets(cls)
        # We can also load backbone presets.
        if cls.backbone_cls is not None:
            presets.update(cls.backbone_cls.presets)
        for subclass in list_subclasses(cls):
            presets.update(subclass.presets)
        return presets

    @classmethod
    def from_preset(
        cls,
        preset,
        load_weights=True,
        **kwargs,
    ):
        """Instantiate a `keras_nlp.models.Task` from a model preset.

        A preset is a directory of configs, weights and other file assets used
        to save and load a pre-trained model. The `preset` can be passed as a
        one of:

        1. a built in preset identifier like `'bert_base_en'`
        2. a Kaggle Models handle like `'kaggle://user/bert/keras/bert_base_en'`
        3. a Hugging Face handle like `'hf://user/bert_base_en'`
        4. a path to a local preset directory like `'./bert_base_en'`

        For any `Task` subclass, you can run `cls.presets.keys()` to list all
        built-in presets available on the class.

        This constructor can be called in one of two ways. Either from a task
        specific base class like `keras_nlp.models.CausalLM.from_preset()`, or
        from a model class like `keras_nlp.models.BertClassifier.from_preset()`.
        If calling from the a base class, the subclass of the returning object
        will be inferred from the config in the preset directory.

        Args:
            preset: string. A built in preset identifier, a Kaggle Models
                handle, a Hugging Face handle, or a path to a local directory.
            load_weights: bool. If `True`, the weights will be loaded into the
                model architecture. If `False`, the weights will be randomly
                initialized.

        Examples:
        ```python
        # Load a Gemma generative task.
        causal_lm = keras_nlp.models.CausalLM.from_preset(
            "gemma_2b_en",
        )

        # Load a Bert classification task.
        model = keras_nlp.models.Classifier.from_preset(
            "bert_base_en",
            num_classes=2,
        )
        ```
        """
        if cls == Task:
            raise ValueError(
                "Do not call `Task.from_preset()` directly. Instead call a "
                "particular task class, e.g. "
                "`keras_nlp.models.Classifier.from_preset()` or "
                "`keras_nlp.models.BertClassifier.from_preset()`."
            )

        loader = get_preset_loader(preset)
        backbone_cls = loader.check_backbone_class()
        # Detect the correct subclass if we need to.
        if cls.backbone_cls != backbone_cls:
            cls = find_subclass(preset, cls, backbone_cls)
        return loader.load_task(cls, load_weights, **kwargs)

    def load_task_weights(self, filepath):
        """Load only the tasks specific weights not in the backbone."""
        if not str(filepath).endswith(".weights.h5"):
            raise ValueError(
                "The filename must end in `.weights.h5`. Received: filepath={filepath}"
            )
        backbone_layer_ids = set(id(w) for w in self.backbone._flatten_layers())
        keras.saving.load_weights(
            self,
            filepath,
            objects_to_skip=backbone_layer_ids,
        )

    def has_task_weights(self):
        task_weight_ids = set(id(w) for w in self.weights)
        backbone_weight_ids = set(id(w) for w in self.backbone.weights)
        return not task_weight_ids.issubset(backbone_weight_ids)

    def save_task_weights(self, filepath):
        """Save only the tasks specific weights not in the backbone."""
        if not str(filepath).endswith(".weights.h5"):
            raise ValueError(
                "The filename must end in `.weights.h5`. "
                f"Received: filepath={filepath}"
            )

        backbone_layer_ids = set(id(w) for w in self.backbone._flatten_layers())
        if not self.has_task_weights():
            raise ValueError(
                f"Task {self} has no weights not in the `backbone`. "
                "`save_task_weights()` has nothing to save."
            )
        keras.saving.save_weights(
            self,
            filepath=filepath,
            objects_to_skip=backbone_layer_ids,
        )

    def save_to_preset(self, preset_dir):
        """Save task to a preset directory.

        Args:
            preset_dir: The path to the local model preset directory.
        """
        if self.preprocessor is None:
            raise ValueError(
                "Cannot save `task` to preset: `Preprocessor` is not initialized."
            )

        save_serialized_object(self, preset_dir, config_file=TASK_CONFIG_FILE)
        if self.has_task_weights():
            self.save_task_weights(os.path.join(preset_dir, TASK_WEIGHTS_FILE))

        self.preprocessor.save_to_preset(preset_dir)
        self.backbone.save_to_preset(preset_dir)

    @property
    def layers(self):
        # Remove preprocessor from layers so it does not show up in the summary.
        layers = super().layers
        if self.preprocessor and self.preprocessor in layers:
            layers.remove(self.preprocessor)
        return layers

    def summary(
        self,
        line_length=None,
        positions=None,
        print_fn=None,
        **kwargs,
    ):
        """Override `model.summary()` to show a preprocessor if set."""

        # Compat fixes for tf.keras.
        if not hasattr(self, "compiled"):
            self.compiled = getattr(self.optimizer, "_is_compiled", False)
        if (
            self.compiled
            and self.optimizer
            and not hasattr(self.optimizer, "built")
        ):
            self.optimizer.built = getattr(self.optimizer, "_built", False)

        # Below is copied from keras-core for now.
        # We should consider an API contract.
        line_length = line_length or 108

        if not print_fn and not keras.utils.is_interactive_logging_enabled():
            print_fn = print_msg

        def highlight_number(x):
            return f"[color(45)]{x}[/]" if x is None else f"[color(34)]{x}[/]"

        def highlight_symbol(x):
            return f"[color(33)]{x}[/]"

        def bold_text(x):
            return f"[bold]{x}[/]"

        if self.preprocessor:
            # Create a rich console for printing. Capture for non-interactive logging.
            if print_fn:
                console = rich_console.Console(
                    highlight=False, force_terminal=False, color_system=None
                )
                console.begin_capture()
            else:
                console = rich_console.Console(highlight=False)

            column_1 = rich_table.Column(
                "Tokenizer (type)",
                justify="left",
                width=int(0.5 * line_length),
            )
            column_2 = rich_table.Column(
                "Vocab #",
                justify="right",
                width=int(0.5 * line_length),
            )
            table = rich_table.Table(
                column_1, column_2, width=line_length, show_lines=True
            )
            tokenizer = self.preprocessor.tokenizer
            tokenizer_name = markup.escape(tokenizer.name)
            tokenizer_class = highlight_symbol(
                markup.escape(tokenizer.__class__.__name__)
            )
            table.add_row(
                f"{tokenizer_name} ({tokenizer_class})",
                highlight_number(f"{tokenizer.vocabulary_size():,}"),
            )

            # Print the to the console.
            preprocessor_name = markup.escape(self.preprocessor.name)
            console.print(bold_text(f'Preprocessor: "{preprocessor_name}"'))
            console.print(table)

            # Output captured summary for non-interactive logging.
            if print_fn:
                print_fn(console.end_capture(), line_break=False)

        super().summary(
            line_length=line_length,
            positions=positions,
            print_fn=print_fn,
            **kwargs,
        )
