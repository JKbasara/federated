# Copyright 2018, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Utilities for Python functions, defuns, and other types of callables."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import inspect
import types as py_types

from tensorflow.python.framework import function as tf_function

from tensorflow_federated.python.core.api import types

from tensorflow_federated.python.core.impl import anonymous_tuple
from tensorflow_federated.python.core.impl import type_utils


def is_defun(func):
  """Determines whether 'func' is one of the known types of TF defuns.

  Args:
    func: The object to test for being a supported type of a TensorFlow defun.

  Returns:
    True iff 'func' is a supported type of a TF defun.
  """
  return isinstance(func, (
      # TODO(b/113112885): Add support for tfe Function and PolymorphicFunction,
      # currently omitted due to issues with visibility.

      # While these classes can be private to TF users, we need to peek into
      # the private interfaces of these classes in order to obtain the function
      # signatures and type information that are otherwise unavailable via
      # regular public APIs. In order to do so safelty, we need to narrow the
      # scope down to a few concrete classes, internal structure we create a
      # dependency on.
      # TODO(b/113112885): Work towards avoiding this, posisbly by upstreaming
      # some helper library or extending the public interface.
      # pylint: disable=protected-access
      tf_function._DefinedFunction,
      tf_function._OverloadedFunction))
      # pylint: enable=protected-access


def get_argspec(func):
  """Returns the inspect.ArgSpec structure for the given function/defun 'func'.

  Args:
    func: The Python function or defun to analyze.

  Returns:
    The corresponding instance of inspect.ArgSpec.

  Raises:
    TypeError: if the argument is not of a supported type.
  """
  if isinstance(func, py_types.FunctionType):
    return inspect.getargspec(func)
  # TODO(b/113112885): Add support for tfe Function and PolymorphicFunction,
  # currently omitted due to issues with visibility, using tf_inspect.getargspec
  # that works in eager mode.
  elif isinstance(func, (
      # There does not appear to be a robust way to distinguish between typed
      # and polymorphic defuns, so we refer to private class names again.
      # pylint: disable=protected-access
      tf_function._DefinedFunction, tf_function._OverloadedFunction)):
      # pylint: enable=protected-access
    # On the non-eager defuns, tf_inspect does not appear to work, so we peek
    # inside to extract arguments.
    # pylint: disable=protected-access
    return inspect.getargspec(func._func)
    # pylint: enable=protected-access
  elif is_defun(func):
    raise TypeError(
        'Support for defuns of type {} has not been implemented yet.'.format(
            type(func).__name__))
  else:
    raise TypeError(
        'Expected a Python function or a defun, found {}.'.format(
            type(func).__name__))


def get_callargs_for_argspec(argspec, *args, **kwargs):
  """Similar to inspect.getcallargs(), but accepts inspect.ArgSpec instead.

  This function allows getcallargs() capability to be used with defuns and
  other types of callables that aren't Python functions.

  Args:
    argspec: An instance of inspect.ArgSpec to assign arguments to.
    *args: Positional arguments.
    **kwargs: Keyword-based arguments.

  Returns:
    The same type of result as what inspect.getcallargs() returns.

  Raises:
    TypeError: if the arguments are of the wrong types, or if the 'args' and
      'kwargs' combo is not compatible with 'argspec'.
  """
  if not isinstance(argspec, inspect.ArgSpec):
    raise TypeError('Expected {}, found {}.'.format(
        type(inspect.ArgSpec).__name__, type(argspec).__name__))
  result = {}
  num_specargs = len(argspec.args) if argspec.args else 0
  num_defaults = len(argspec.defaults) if argspec.defaults else 0
  num_specargs_without_defaults = num_specargs - num_defaults
  if len(args) > num_specargs and not argspec.varargs:
    raise TypeError(
        'Too many positional arguments for the call: expected at most {}, '
        'found {}.'.format(num_specargs, len(args)))
  for idx, specarg in enumerate(argspec.args):
    if idx < len(args):
      if specarg in kwargs:
        raise TypeError('Argument {} specified twice.'.format(specarg))
      result[specarg] = args[idx]
    elif specarg in kwargs:
      result[specarg] = kwargs[specarg]
    elif idx >= num_specargs_without_defaults:
      result[specarg] = argspec.defaults[idx - num_specargs_without_defaults]
    else:
      raise TypeError(
          'Argument {} was not specified and does not have a default.'.format(
              specarg))
  unused_kwargs = {k: v for k, v in kwargs.iteritems() if k not in result}
  if argspec.varargs:
    result[argspec.varargs] = args[num_specargs:]
  if argspec.keywords:
    result[argspec.keywords] = unused_kwargs
  elif unused_kwargs:
    raise TypeError('Unexpected keyword arguments in the call: {}'.format(
        unused_kwargs))
  return result


def is_argspec_compatible_with_types(argspec, *args, **kwargs):
  """Determines if functions matching 'argspec' accept given 'args'/'kwargs'.

  Args:
    argspec: An instance of inspect.ArgSpec to verify agains the arguments.
    *args: Zero or more positional arguments, all of which must be instances of
      types.Type or something convertible to it by types.to_type().
    **kwargs: Zero or more keyword arguments, all of which must be instances of
      types.Type or something convertible to it by types.to_type().

  Returns:
    True or false, depending on the outcome of the test.

  Raises:
    TypeError: if the arguments are of the wrong types.
  """
  try:
    callargs = get_callargs_for_argspec(argspec, *args, **kwargs)
    if not argspec.defaults:
      return True
  except TypeError:
    return False

  # As long as we have been able to construct 'callargs', and there are no
  # default values to verify against the given types, there is nothing more
  # to do here, otherwise we have to verify the types of defaults against
  # the types we've been given as parameters to this function.
  num_specargs_without_defaults = len(argspec.args) - len(argspec.defaults)
  for idx, default_value in enumerate(argspec.defaults):
    if default_value is not None:
      arg_name = argspec.args[num_specargs_without_defaults + idx]
      call_arg = callargs[arg_name]
      if call_arg is not default_value:
        arg_type = types.to_type(call_arg)
        default_type = type_utils.infer_type(default_value)
        if not arg_type.is_assignable_from(default_type):
          return False
  return True


def is_argument_tuple(arg):
  """Determines if 'arg' is interpretable as an argument tuple.

  Args:
    arg: A value or type to test.

  Returns:
    True iff 'arg' is either an anonymous tuple in which all unnamed elements
    precede named ones, or a named tuple typle with this property, or something
    that can be converted into the latter by types.to_type().

  Raises:
    TypeError: if the argument is neither an AnonymousTuple, nor a type spec.
  """
  if isinstance(arg, anonymous_tuple.AnonymousTuple):
    elements = anonymous_tuple.to_elements(arg)
  else:
    arg = types.to_type(arg)
    if isinstance(arg, types.NamedTupleType):
      elements = arg.elements
    else:
      return False
  max_unnamed = -1
  min_named = len(elements)
  for idx, element in enumerate(elements):
    if element[0]:
      min_named = min(min_named, idx)
    else:
      max_unnamed = idx
  return max_unnamed < min_named


def unpack_args_from_tuple(tuple_with_args):
  """Extracts argument types from a named tuple type.

  Args:
    tuple_with_args: An instance of either an AnonymousTuple or
      types.NamedTupleType (or something convertible to it by types.to_type()),
      on which is_argument_tuple() is True.

  Returns:
    A pair (args, kwargs) containing tuple elements from 'tuple_with_args'.

  Raises:
    TypeError: if 'tuple_with_args' is of a wrong type.
  """
  if isinstance(tuple_with_args, anonymous_tuple.AnonymousTuple):
    elements = anonymous_tuple.to_elements(tuple_with_args)
  else:
    tuple_with_args = types.to_type(tuple_with_args)
    if isinstance(tuple_with_args, types.NamedTupleType):
      elements = tuple_with_args.elements
    else:
      raise TypeError('Expected an argument tuple, found {}.'.format(
          type(tuple_with_args).__name__))
  args = []
  kwargs = {}
  for e in elements:
    if e[0]:
      kwargs[e[0]] = e[1]
    else:
      args.append(e[1])
  return (args, kwargs)


def pack_args_into_anonymous_tuple(*args, **kwargs):
  """Packs positional and keyword arguments into an anonymous tuple.

  Args:
    *args: Positional arguments.
    **kwargs: Keyword arguments.

  Returns:
    An anoymous tuple containing all the arguments.
  """
  return anonymous_tuple.AnonymousTuple(
      [(None, arg) for arg in args] + list(kwargs.iteritems()))


def wrap_as_zero_or_one_arg_callable(func, parameter_type=None, unpack=None):
  """Wraps around 'func' so it accepts up to one positional TFF-typed argument.

  This function helps to simplify dealing with functions and defuns that might
  have diverse and complex signatures, but that represent computations and as
  such, conceptually only accept a single parameter. The returned callable has
  a single positional parameter or no parameters. If it has one parameter, the
  parameter is expected to contain all arguments required by 'func' and matching
  the supplied parameter type signature bundled together into an anonymous
  tuple, if needed. The callable unpacks that structure, and passes all of
  its elements as positional or keyword-based arguments in the call to 'func'.

  Example usage:

    @tf.contrib.eager.defun
    def my_func(x, y, z=10, name='bar', *p, **q):
      return x + y

    type_spec = (tf.int32, tf.int32)

    wrapped_fn = wrap_as_zero_or_one_arg_callable(my_func, type_spec)

    arg = AnonymoutTuple([('x', 10), ('y', 20)])

    ... = wrapped_fn(arg)

  Args:
    func: The underlying backend function or defun to invoke with the unpacked
      arguments.
    parameter_type: The TFF type of the parameter bundle to be accepted by the
      returned callable, if any, or None if there's no parameter.
    unpack: Whether to break the parameter down into constituent parts and feed
      them as arguments to 'func' (True), leave the parameter as is and pass it
      to 'func' as a single unit (False), or allow it to be inferred from the
      signature of 'func' (None). In the latter case (None), if any ambiguity
      arises, an exception is thrown. If the parameter_type is None, this value
      has no effect, and is simply ignored.

  Returns:
    The zero- or one-argument callable that invokes 'func' with the unbundled
    arguments, as described above.

  Raises:
    TypeError: if arguments to this call are of the wrong types, or if the
      supplied 'parameter_type' is not compatible with 'func'.
  """
  # TODO(b/113112885): Revisit whether the 3-way 'unpack' knob is sufficient
  # for our needs, or more options are needed.
  if unpack not in [True, False, None]:
    raise TypeError(
        'The unpack argument has an unexpected value {}.'.format(repr(unpack)))
  argspec = get_argspec(func)
  parameter_type = types.to_type(parameter_type)
  if not parameter_type:
    if is_argspec_compatible_with_types(argspec):
      # Deliberate wrapping to isolate the caller from 'func', e.g., to prevent
      # the caller from mistakenly specifying args that match func's defaults.
      # pylint: disable=unnecessary-lambda
      return lambda: func()
      # pylint: enable=unnecessary-lambda
    else:
      raise TypeError(
          'The argspec {} of the supplied function cannot be interpreted as a '
          'body of a no-parameter computation.'.format(str(argspec)))
  else:
    unpack_required = not is_argspec_compatible_with_types(
        argspec, parameter_type)
    if unpack_required and unpack is False:
      raise TypeError(
          'The supplied function with argspec {} cannot accept a value of '
          'type {} as a single arghument.'.format(
              str(argspec), str(parameter_type)))
    if is_argument_tuple(parameter_type):
      arg_types, kwarg_types = unpack_args_from_tuple(parameter_type)
      unpack_possible = is_argspec_compatible_with_types(
          argspec, *arg_types, **kwarg_types)
    else:
      unpack_possible = False
    if not unpack_possible and unpack is True:
      raise TypeError(
          'The supplied function with argspec {} cannot accept a value of '
          'type {} as multiple positional and/or keyword arguments.'.format(
              str(argspec), str(parameter_type)))
    if unpack_required and not unpack_possible:
      raise TypeError(
          'The supplied function with argspec {} cannot accept a value of '
          'type {} as either a single argument or multiple positional and/or '
          'keyword arguments.'.format(
              str(argspec), str(parameter_type)))
    if not unpack_required and unpack_possible and unpack is None:
      raise TypeError(
          'The supplied function with argspec {} could accept a value of '
          'type {} as either a single argument or multiple positional and/or '
          'keyword arguments, and the caller did not specify any preference, '
          'leaving ambiguity in how to handle the mapping.'.format(
              str(argspec), str(parameter_type)))
    if unpack is None:
      # Any ambiguity at this point has been resolved, so the following
      # condition holds and need only be verified in tests.
      assert unpack_required == unpack_possible
      unpack = unpack_possible
    if unpack:
      def _unpack_and_call(func, arg_types, kwarg_types, arg):
        """An interceptor function that unpacks 'arg' before calling 'func'.

        The function verifies the actual parameters before it forwards the
        call as a last-minute check.

        Args:
          func: The function or defun to invoke.
          arg_types: The list of positional argument types (guaranteed to all
            be instances of types.Types).
          kwarg_types: The dictionary of keyword argument types (guaranteed to
            all be instances of types.Types).
          arg: The argument to unpack.

        Returns:
          The result of invoking 'func' on the unpacked arguments.

        Raises:
          TypeError: if types don't match.
        """
        if not isinstance(arg, anonymous_tuple.AnonymousTuple):
          raise TypeError('Expected {}, found {}.'.format(
              type(anonymous_tuple.AnonymousTuple).__name__,
              type(arg).__name__))
        args = []
        for idx, expected_type in enumerate(arg_types):
          element_value = arg[idx]
          actual_type = type_utils.infer_type(element_value)
          if not expected_type.is_assignable_from(actual_type):
            raise TypeError(
                'Expected element at position {} to be '
                'of type {}, found {}.'.format(
                    idx, str(expected_type), str(actual_type)))
          args.append(element_value)
        kwargs = {}
        for name, expected_type in kwarg_types.iteritems():
          element_value = getattr(arg, name)
          actual_type = type_utils.infer_type(element_value)
          if not expected_type.is_assignable_from(actual_type):
            raise TypeError(
                'Expected element named {} to be '
                'of type {}, found {}.'.format(
                    name, str(expected_type), str(actual_type)))
          kwargs[name] = element_value
        return func(*args, **kwargs)
      # Deliberate wrapping to isolate the caller from the underlying function
      # and the interceptor '_call' again, so those cannot be tampered with,
      # and to force any parameter bindings to be resolved now.
      # pylint: disable=unnecessary-lambda,undefined-variable
      return (lambda fn, at, kt: lambda arg: _unpack_and_call(fn, at, kt, arg))(
          func, arg_types, kwarg_types)
      # pylint: enable=unnecessary-lambda,undefined-variable
    else:
      # An interceptor function that verifies the actual parameter before it
      # forwards the call as a last-minute check.
      def _call(func, parameter_type, arg):
        arg_type = type_utils.infer_type(arg)
        if not parameter_type.is_assignable_from(arg_type):
          raise TypeError('Expected an argument of type {}, found {}.'.format(
              str(parameter_type), str(arg_type)))
        return func(arg)
      # Deliberate wrapping to isolate the caller from the underlying function
      # and the interceptor '_call' again, so those cannot be tampered with,
      # and to force any parameter bindings to be resolved now.
      # pylint: disable=unnecessary-lambda,undefined-variable
      return (lambda fn, pt: lambda arg: _call(fn, pt, arg))(
          func, parameter_type)
      # pylint: enable=unnecessary-lambda,undefined-variable


class PolymorphicFunction(object):
  """A generic polymorphic function that accepts arguments of diverse types."""

  def __init__(self, concrete_function_factory):
    """Crates a polymorphic function with a given function factory.

    Args:
      concrete_function_factory: A callable that accepts a (non-None) TFF type
        as an argument, and returns a single-parameter concrete function that's
        been instantiated to accept an argument of this type.
    """
    self._concrete_function_factory = concrete_function_factory
    self._concrete_function_cache = {}

  def __call__(self, *args, **kwargs):
    """Invokes this polymorphic function with a given set of arguments.

    Args:
      *args: Positional args.
      **kwargs: Keyword args.

    Returns:
      The result of calling a concrete function, instantiated on demand based
      on the argument types (and cached for future calls).
    """
    # TODO(b/113112885): We may need to normalize individuals args, such that
    # the type is more predictable and uniform (e.g., if someone supplies an
    # unordered dictionary), possibly by converting dict-like and tuple-like
    # containters into anonymous tuples.
    packed_arg = pack_args_into_anonymous_tuple(*args, **kwargs)
    arg_type = type_utils.infer_type(packed_arg)
    key = repr(arg_type)
    concrete_fn = self._concrete_function_cache.get(key)
    if not concrete_fn:
      concrete_fn = self._concrete_function_factory(arg_type)
      self._concrete_function_cache[key] = concrete_fn
    return concrete_fn(*args, **kwargs)