import paddle
from paddle.fluid.framework import IrGraph
from paddle.fluid import core
import os
from paddle.fluid.contrib.slim.quantization import QuantizationTransformPass, QuantizationTransformPassV2, AddQuantDequantPass, AddQuantDequantPassV2, QuantizationFreezePass, QuantWeightPass

try:
    from paddle.fluid.contrib.slim.quantization import utils
    TRANSFORM_PASS_OP_TYPES = utils._weight_supported_quantizable_op_type
    QUANT_DEQUANT_PASS_OP_TYPES = utils._act_supported_quantizable_op_type
except:
    TRANSFORM_PASS_OP_TYPES = QuantizationTransformPass._supported_quantizable_op_type
    QUANT_DEQUANT_PASS_OP_TYPES = AddQuantDequantPass._supported_quantizable_op_type


def post_quant_fake(executor,
                    model_dir,
                    model_filename=None,
                    params_filename=None,
                    save_model_path=None,
                    quantizable_op_type=["conv2d", "depthwise_conv2d", "mul"],
                    is_full_quantize=False,
                    activation_bits=8,
                    weight_bits=8,
                    onnx_format=False):
    """
    Utilizing post training quantization methon to quantize the FP32 model,
    and it not uses calibrate data and the fake model cannot be used in practice.
    Usage:
        paddle.enable_static()
        place = paddle.CPUPlace()
        exe = paddle.static.Executor(place)
        post_quant_fake(executor=exe,
            model_dir='./inference_model/MobileNet/',
            model_filename='model',
            params_filename='params',
            save_model_path='fake_quant')
    """
    activation_quantize_type = 'range_abs_max'
    weight_quantize_type = 'channel_wise_abs_max'
    _dynamic_quantize_op_type = ['lstm']
    _weight_supported_quantizable_op_type = TRANSFORM_PASS_OP_TYPES
    _act_supported_quantizable_op_type = QUANT_DEQUANT_PASS_OP_TYPES
    _support_quantize_op_type = list(
        set(_weight_supported_quantizable_op_type +
            _act_supported_quantizable_op_type + _dynamic_quantize_op_type))
    _place = executor.place
    _scope = paddle.static.global_scope()
    if is_full_quantize:
        _quantizable_op_type = _support_quantize_op_type
    else:
        _quantizable_op_type = quantizable_op_type
        for op_type in _quantizable_op_type:
            assert op_type in _support_quantize_op_type, \
                op_type + " is not supported for quantization."

    _program, _feed_list, _fetch_list = paddle.fluid.io.load_inference_model(
        model_dir,
        executor,
        model_filename=model_filename,
        params_filename=params_filename)

    graph = IrGraph(core.Graph(_program.desc), for_test=True)

    # use QuantizationTransformPass to insert fake_quant/fake_dequantize op
    major_quantizable_op_types = []
    for op_type in _weight_supported_quantizable_op_type:
        if op_type in _quantizable_op_type:
            major_quantizable_op_types.append(op_type)
    if onnx_format:
        transform_pass = QuantizationTransformPassV2(
            scope=_scope,
            place=_place,
            weight_bits=weight_bits,
            activation_bits=activation_bits,
            activation_quantize_type=activation_quantize_type,
            weight_quantize_type=weight_quantize_type,
            quantizable_op_type=major_quantizable_op_types)
    else:
        transform_pass = QuantizationTransformPass(
            scope=_scope,
            place=_place,
            weight_bits=weight_bits,
            activation_bits=activation_bits,
            activation_quantize_type=activation_quantize_type,
            weight_quantize_type=weight_quantize_type,
            quantizable_op_type=major_quantizable_op_types)

    for sub_graph in graph.all_sub_graphs():
        # Insert fake_quant/fake_dequantize op must in test graph, so
        # set per graph's _for_test is True.
        sub_graph._for_test = True
        transform_pass.apply(sub_graph)

    # use AddQuantDequantPass to insert fake_quant_dequant op
    minor_quantizable_op_types = []
    for op_type in _act_supported_quantizable_op_type:
        if op_type in _quantizable_op_type:
            minor_quantizable_op_types.append(op_type)
    if onnx_format:
        add_quant_dequant_pass = AddQuantDequantPassV2(
            scope=_scope,
            place=_place,
            quantizable_op_type=minor_quantizable_op_types,
            is_full_quantized=is_full_quantize)
    else:
        add_quant_dequant_pass = AddQuantDequantPass(
            scope=_scope,
            place=_place,
            quantizable_op_type=minor_quantizable_op_types)

    for sub_graph in graph.all_sub_graphs():
        sub_graph._for_test = True
        add_quant_dequant_pass.apply(sub_graph)

    # apply QuantizationFreezePass, and obtain the final quant model
    if onnx_format:
        quant_weight_pass = QuantWeightPass(_scope, _place)
        for sub_graph in graph.all_sub_graphs():
            sub_graph._for_test = True
        quant_weight_pass.apply(sub_graph)
    else:
        freeze_pass = QuantizationFreezePass(
            scope=_scope,
            place=_place,
            weight_bits=weight_bits,
            activation_bits=activation_bits,
            weight_quantize_type=weight_quantize_type,
            quantizable_op_type=major_quantizable_op_types)

        for sub_graph in graph.all_sub_graphs():
            sub_graph._for_test = True
            freeze_pass.apply(sub_graph)

    _program = graph.to_program()

    paddle.fluid.io.save_inference_model(
        dirname=save_model_path,
        model_filename=model_filename,
        params_filename=params_filename,
        feeded_var_names=_feed_list,
        target_vars=_fetch_list,
        executor=executor,
        main_program=_program)
    save_quant_table_path = os.path.join(save_model_path,
                                         'calibration_table.txt')
    with open(save_quant_table_path, 'w') as txt_file:
        txt_file.write("Fake_Quantize_Demo.")
    print("The quantized model is saved in: " + save_model_path)


if __name__ == '__main__':
    paddle.enable_static()
    place = paddle.CPUPlace()
    exe = paddle.static.Executor(place)
    # post_quant_fake(executor=exe,
    #     model_dir='/paddle/picodet/0519/PaddleDetection_v2/output_inference/retinanet_r50_fpn_1x_coco/',
    #     model_filename='model.pdmodel',
    #     params_filename='model.pdiparams',
    #     save_model_path='retinanet_r50_fpn_fake_quant')
    post_quant_fake(
        executor=exe,
        model_dir='./videofeedasq//',
        model_filename='__model__',
        params_filename='__params__',
        save_model_path='videofeedasq_onnx',
        is_full_quantize=True,
        onnx_format=True)
