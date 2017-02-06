"""
kernel_gen.py - generators used for kernel creation
"""

import textwrap

import loopy as lp

from .file_writers import file_writer as fwrite
from .memory_manager import memory_manager
from .. import site_conf as site

script_dir = os.path.abspath(os.path.dirname(__file__))

class wrapping_kernel_generator(object):
    def __init__(self, loopy_opts, name, kernels,
        input_arrays=[], output_arrays=[], init_arrays={},
        test_size=None, auto_diff=False):
        """
        Parameters
        ----------
        loopy_opts : :class:`LoopyOptions`
            The specified user options
        name : str
            The kernel name to use
        kernels : list of :class:`loopy.LoopKernel`
            The kernels / calls to wrap
        input_arrays : list of str
            The names of the input arrays of this kernel
        output_arrays : list of str
            The names of the output arrays of this kernel
        init_arrays : dict
            A mapping of name -> initializer value for arrays in
            this kernel that require constant value initalization
        test_size : int
            If specified, the # of conditions to test
        auto_diff : bool
            If true, this will be used for automatic differentiation
        """

        self.loopy_opts = loopy_opts
        self.lang = loopy_opts.lang
        self.mem = memory_manager(self.lang)
        self.name = name
        self.kernels = kernels
        self.test_size = test_size

        #update the memory manager
        self.mem.add_arrays(input_arrays=input_arrays,
            output_arrays=output_arrays, has_init=init_arrays)

        self.kernel_arg_set_template = Template(self.mem.get_check_err_call('clSetKernelArg(kernel,'
                                        '${arg_index}, ${arg_size}, ${arg_value})'))

        self.filename = ''
        self.bin_name = ''

    def generate(self, path):
        """
        Generates wrapping kernel, compiling program (if necessary) and
        calling / executing program for this kernel

        Parameters
        ----------
        path : str
            The output path

        Returns
        -------
        None
        """
        self._generate_wrapping_kernel(path)
        self._generate_compiling_program(path)
        self._generate_calling_program(path)

    def _generate_calling_program(self, path):
        """
        Needed for all languages, this generates a simple C file that
        reads in data, sets up the kernel call, executes, etc.

        Parameters
        ----------
        path : str
            The output path to write files to

        Returns
        -------
        None
        """

        assert self.filename or self.bin_name, 'Cannot generate compiler before wrapping kernel is generated...'

        #find definitions
        mem_declares = self.mem.get_defns()
        def __get_pass(arg):
            return '{}* {}'.format(utils.type_map[arg.dtype], arg.name)
        #and input args
        knl_args = self.mem.input_arrays[:] + self.mem.output_arrays[:]
        knl_args = ', '.join([__get_pass(a) for a in knl_args])
        #create doc str
        knl_args_doc = []
        knl_args_doc_template = Template(
"""
${name} : ${type}
    ${desc}
""")
        for x in knl_args:
            if x == 'T_arr':
                knl_args_doc.append(knl_args_doc_template.safe_substitute(
                    name=x, type='double*', desc='The array of temperatures'))
            elif x == 'P_arr':
                knl_args_doc.append(knl_args_doc_template.safe_substitute(
                    name=x, type='double*', desc='The array of pressures'))
            elif x == 'spec_rates':
                knl_args_doc.append(knl_args_doc_template.safe_substitute(
                    name=x, type='double*', desc='The array of species rates, in {}-order').format(
                    self.loopy_opts.order))
            else:
                raise Exception('Argument documentation not found for arg {}'.format(x))

        #memory transfers in
        mem_in = self.mem.get_mem_transfers_in()
        #memory transfers out
        mem_out = self.mem.get_mem_transfers_out()
        #vec width
        vec_width = self.vec_width
        #kernel
        kernel_path = self.bin_name if self.bin_name else self.filename
        #platform
        platform_str = self.loopy_opts.platform.get_info(cl.platform_info.VENDOR)
        #build options
        build_options = self.build_options
        #memory allocations
        mem_allocs = self.mem.get_mem_allocs()
        #kernel arg setting
        kernel_arg_sets = self.get_kernel_arg_setting()
        #memory frees
        mem_frees = self.mem.get_mem_frees()

        #get template
        with open(os.path.join(script_dir, self.lang,
                    'kernel.c.in'), 'r') as file:
            file_src = Template(file.read())

        with filew.get_file(os.path.join(path, self.name +
            utils.file_ext[self.loopy_opts.lang]),
                            self.loopy_opts.lang) as file:
                file.add_lines(file_src.safe_substitute(
                    mem_declares=mem_declares,
                    outname=self.filename + '.bin',
                    platform=platform_str,
                    build_options=self.build_options
                    ))


    def get_kernel_arg_setting(self):
        """
        Needed for OpenCL, this generates the code that sets the kernel args

        Parameters
        ----------
        None

        Returns
        -------
        knl_arg_set_str : str
            The code that sets opencl kernel args
        """

        kernel_arg_sets = []
        for i, arg in self.mem.arrays:
            kernel_arg_sets.append(
                self.kernel_arg_set_template.safe_substitute(
                    arg_index=i,
                    arg_size=self.mem._get_size(arg, subs_n='problem_size') +
                        'sizeof({})'.format(utils.type_map[arg.dtype]),
                    arg_value=arg.name)
                    )

        return '\n'.join([x + utils.line_end[self.lang] for x in kernel_arg_sets])

    def _generate_compilation_program(self, path):
        """
        Needed for OpenCL, this generates a simple C file that
        compiles and stores the binary OpenCL kernel generated w/ the wrapper

        Parameters
        ----------
        path : str
            The output path to write files to

        Returns
        -------
        None
        """

        assert self.filename, 'Cannot generate compiler before wrapping kernel is generated...'

        self.build_options = ''
        if self.lang == 'opencl':
            with open(os.path.join(script_dir, self.lang,
                    'opencl_kernel_compiler.c.in'),
                 'r') as file:
                file_str = file.read()
                file_src = Template(file_str)

            #get the platform from the options
            platform_str = self.loopy_opts.platform.get_info(cl.platform_info.VENDOR)

            #for the build options, we turn to the siteconf
            self.build_options = ['-I' + site.CL_INC_DIR, '-I' + path]
            self.build_options.extend(site.CL_FLAGS)
            self.build_options.append('-cl-std=CL{}'.format(site.CL_VERSION))
            self.build_options = ' '.join(build_options)

            with filew.get_file(os.path.join(path, self.name + '_compiler'
                                 + utils.file_ext[self.loopy_opts.lang]),
                            self.loopy_opts.lang) as file:
                file.add_lines(file_src.safe_substitute(
                    filename=self.filename,
                    outname=self.filename + '.bin',
                    platform=platform_str,
                    build_options=self.build_options
                    ))


    def _generate_wrapping_kernel(self, path):
        """
        Generates the wrapping kernel

        Parameters
        ----------
        path : str
            The output path to write files to

        Returns
        -------
        None
        """
        file_prefix = ''
        if self.auto_diff:
            file_prefix = 'ad_'

        #first, load the wrapper as a template
        with open(os.path.join(script_dir, self.lang,
                    'wrapping_kernel.{}.in'.format(utils.file_ext[self.lang])),
                 'r') as file:
            file_str = file.read()
            file_src = Template(file_str)

        #create T / P arrays
        kernel_data = []
        T_arr = lp.GlobalArg('T_arr',
                        shape=(test_size),
                        order=self.loopy_opts.order,
                        dtype=np.float64,
                        read_only=True)
        P_arr = lp.GlobalArg('P_arr',
                        shape=(test_size),
                        order=self.loopy_opts.order,
                        dtype=np.float64,
                        read_only=True)
        concs_arr = lp.GlobalArg('conc',
                        shape=('Ns', test_size),
                        order=self.loopy_opts.order,
                        dtype=np.float64,
                        read_only=True)
        if test_size == 'n':
            kernel_data += [lp.ValueArg(test_size, dtype=np.int32)]
        kernel_data.extend([T_arr, P_arr, concs_arr])
        self.mem.add_arrays(in_arrays=[T_arr.name, P_arr.name, concs_arr.name])

        #Finally, turn various needed into ours
        defines = [arg for knl in kernels for arg in knl.args if
                        not isinstance(arg, lp.TemporaryVariable)
                        and arg not in kernel_data]
        nameset = sorted(set(d.name for d in defines))
        args = []
        for name in nameset:
            #check for dupes
            same_name = [x for x in defines if x.name == name]
            assert all(same_name[0] == y for y in same_name[1:])
            same_name = same_name[0]
            same_name.read_only = False
            kernel_data.append(same_name)

        #generate the kernel definition
        self.vec_width = self.self.loopy_opts.depth
        if self.vec_width is None:
            self.vec_width = self.loopy_opts.width
        if self.vec_width is None:
            self.vec_width = 0
        #create a dummy kernel to get the defn
        knl = lp.make_kernel('{{[i, j]: 0 <= i,j < {}}}'.format(vec_width),
            '',
            kernel_data,
            name=self.name,
            target=target
            )
        knl = apply_vectorization(self.loopy_opts, 'i', knl)
        defn_str = lp_utils.get_header(knl)

        #next create the call instructions
        def __gen_call(knl, idx, condition=None):
            call = Template('${name}(${args})${end}').safe_substitute(
                    name=knl.name,
                    args=','.join([arg.name for arg in knl.args
                            if not isinstance(arg, lp.TemporaryVariable)]),
                    end=utils.line_end[self.loopy_opts.lang]
                    #dep='id=call_{}{}'.format(idx, ', dep=call_{}'.format(idx - 1) if idx > 0 else '')
                )
            if condition:
                call = Template(
    """
    #ifdef ${cond}
        ${call}
    #endif
    """            ).safe_substitute(cond=condition, call=call)
            return call

        conditions = [None for knl in kernels]
        for i in range(len(kernels)):
            conditions[i] = next((x for x in ['conp', 'conv']
                                    if x in kernels[i].name), None).upper()
        instructions = '\n'.join(__gen_call(knl, i, conditions[i])
            for i, knl in enumerate(kernels))

        #and finally, generate the additional kernels
        additional_kernels = '\n'.join([lp_utils.get_code(k) for k in kernels])

        self.filename = os.path.join(path,
                            file_prefix + self.name + utils.file_ext[self.loopy_opts.lang])
        #create the file
        with filew.get_file(self.filename, self.loopy_opts.lang) as file:
            instructions = __find_indent(file_str, 'body', instructions)
            lines = file_src.safe_substitute(
                        defines='',
                        func_define=defn_str,
                        body=instructions,
                        additional_kernels=additional_kernels).split('\n')

            if auto_diff:
                lines = [x.replace('double', 'adouble') for x in lines]
            file.add_lines(lines)

        #and the header file
        headers = [lp_utils.get_header(knl) + utils.line_end[self.loopy_opts.lang]
                        for knl in kernels] + [defn_str + utils.line_end[self.loopy_opts.lang]]
        with filew.get_header_file(os.path.join(path, file_prefix + self.name
                                 + utils.header_ext[loopy_opts.lang]), loopy_opts.lang) as file:

            lines = '\n'.join(headers).split('\n')
            if self.auto_diff:
                file.add_headers('adept.h')
                file.add_lines('using adept::adouble;\n')
                lines = [x.replace('double', 'adouble') for x in lines]
            file.add_lines(lines)

def handle_indicies(indicies, reac_ind, out_map, kernel_data,
                        outmap_name='out_map', alternate_indicies=None,
                        force_zero=False, force_map=False, scope=scopes.PRIVATE):
    """Consolidates the commonly used indicies mapping steps

    Parameters
    ----------
    indicies: :class:`numpy.ndarray`
        The list of indicies
    reac_ind : str
        The reaction index variable (used in mapping)
    out_map : dict
        The dictionary to store the mapping result in (if any)
    kernel_data : list of :class:`loopy.KernelArgument`
        The data to pass to the kernel (may be added to)
    outmap_name : str, optional
        The name to use in mapping
    alternate_indicies : :class:`numpy.ndarray`
        An alternate list of indicies that can be substituted in to the mapping
    force_zero : bool
        If true, any indicies that don't start with zero require a map (e.g. for
            smaller arrays)
    force_map : bool
        If true, forces use of a map
    scope : :class:`loopy.temp_var_scope`
        The scope of the temporary variable definition, if necessary
    Returns
    -------
    indicies : :class:`numpy.ndarray` OR tuple of int
        The transformed indicies
    """

    check = indicies if alternate_indicies is None else alternate_indicies
    if check[0] + check.size - 1 == check[-1] and \
            (not force_zero or check[0] == 0) and \
            not force_map:
        #if the indicies are contiguous, we can get away with an
        check = (check[0], check[0] + check.size)
    else:
        #need an output map
        out_map[reac_ind] = outmap_name
        #add to kernel data
        outmap_lp = lp.TemporaryVariable(outmap_name,
            shape=lp.auto,
            initializer=check.astype(dtype=np.int32),
            read_only=True, scope=scope)
        kernel_data.append(outmap_lp)

    return check

def apply_vectorization(loopy_opts, inner_ind, knl):
    """
    Applies wide / deep vectorization to a generic rateconst kernel

    Parameters
    ----------
    loopy_opts : :class:`loopy_options` object
        A object containing all the loopy options to execute
    inner_ind : str
        The inner loop index variable
    knl : :class:`loopy.LoopKernel`
        The kernel to transform

    Returns
    -------
    knl : :class:`loopy.LoopKernel`
        The transformed kernel
    """
    #now apply specified optimizations
    if loopy_opts.depth is not None:
        #and assign the l0 axis to 'i'
        knl = lp.split_iname(knl, inner_ind, loopy_opts.depth, inner_tag='l.0')
        #assign g0 to 'j'
        knl = lp.tag_inames(knl, [('j', 'g.0')])
    elif loopy_opts.width is not None:
        #make the kernel a block of specifed width
        knl = lp.split_iname(knl, 'j', loopy_opts.width, inner_tag='l.0')
        #assign g0 to 'i'
        knl = lp.tag_inames(knl, [('j_outer', 'g.0')])

    #now do unr / ilp
    i_tag = inner_ind + '_outer' if loopy_opts.depth is not None else inner_ind
    if loopy_opts.unr is not None:
        knl = lp.split_iname(knl, i_tag, loopy_opts.unr, inner_tag='unr')
    elif loopy_opts.ilp:
        knl = lp.tag_inames(knl, [(i_tag, 'ilp')])

    return knl

class knl_info(object):
    """
    A composite class that contains the various parameters, etc.
    needed to create a simple kernel

    name : str
        The kernel name
    instructions : str or list of str
        The kernel instructions
    pre_instructions : list of str
        The instructions to execute before the inner loop
    post_instructions : list of str
        The instructions to execute after end of inner loop but before end
        of outer loop
    var_name : str
        The inner loop variable
    kernel_data : list of :class:`loopy.ArrayBase`
        The arguements / temporary variables for this kernel
    maps : list of str
        A list of variable mapping instructions
        see :method:`loopy_utils.generate_mapping_instruction`
    extra_inames : list of tuple
        A list of (iname, domain) tuples the form the extra loops in this kernel
    indicies : :class:`numpy.ndarray` or tuple
        The list of indicies to run this kernel on,
        see :method:`handle_indicies`
    assumptions : list of str
        Assumptions to pass to the loopy kernel
    parameters : dict
        Dictionary of parameter values to fix in the loopy kernel
    extra subs : dict
        Dictionary of extra string substitutions to make in kernel generation
    can_vectorize : bool
        If true, can vectorize this kernel
    vectorization_specializer : function
        If specified, use this specialization function to fix problems that would arise
        in vectorization
    """
    def __init__(self, name, instructions, pre_instructions=[],
            post_instructions=[],
            var_name='i', kernel_data=None,
            maps=[], extra_inames=[], indicies=[],
            assumptions=[], parameters={},
            extra_subs={},
            can_vectorize=True,
            vectorization_specializer=None):
        self.name = name
        self.instructions = instructions
        self.pre_instructions = pre_instructions[:]
        self.post_instructions = post_instructions[:]
        self.var_name = var_name
        self.kernel_data = kernel_data[:]
        self.maps = maps[:]
        self.extra_inames = extra_inames[:]
        self.indicies = indicies[:]
        self.assumptions = assumptions[:]
        self.parameters = parameters.copy()
        self.extra_subs = extra_subs
        self.can_vectorize = can_vectorize
        self.vectorization_specializer = vectorization_specializer

def __find_indent(template_str, key, value):
    """
    Finds and returns a formatted value containing the appropriate
    whitespace to put 'value' in place of 'key' for template_str

    Parameters
    ----------
    template_str : str
        The string to sub into
    key : str
        The key in the template string
    value : str
        The string to format

    Returns
    -------
    formatted_value : str
        The formatted string
    """

    #find the instance of ${key} in kernel_str
    whitespace = None
    for i, line in enumerate(template_str.split('\n')):
        if key in line:
            #get whitespace
            whitespace = re.match(r'\s*', line).group()
            break
    result = [line if i == 0 else whitespace + line for i, line in
                enumerate(textwrap.dedent(value).splitlines())]
    return '\n'.join(result)

__TINV_PREINST_KEY = 'Tinv'
__TLOG_PREINST_KEY = 'logT'
__PLOG_PREINST_KEY = 'logP'

def make_kernel(info, target, test_size):
    """
    Convience method to create loopy kernels from kernel_info

    Parameters
    ----------
    info : :class:`knl_info`
        The rate contstant info to generate the kernel from
    target : :class:`loopy.TargetBase`
        The target to generate code for
    test_size : int/str
        The integer (or symbolic) problem size

    Returns
    -------
    knl : :class:`loopy.LoopKernel`
        The generated loopy kernel
    """

    #various precomputes
    pre_inst = {__TINV_PREINST_KEY : '<> T_inv = 1 / T_arr[j]',
                __TLOG_PREINST_KEY : '<> logT = log(T_arr[j])',
                __PLOG_PREINST_KEY : '<> logP = log(P_arr[j])'}

    #and the skeleton kernel
    skeleton = """
    for j
        ${pre}
        for ${var_name}
            ${main}
        end
        ${post}
    end
    """

    #convert instructions into a list for convienence
    instructions = info.instructions
    if isinstance(instructions, str):
        instructions = textwrap.dedent(info.instructions)
        instructions = [x for x in instructions.split('\n') if x.strip()]

    #load inames
    inames = [info.var_name, 'j']

    #add map instructions
    instructions = info.maps + instructions

    #look for extra inames, ranges
    iname_range = []

    assumptions = info.assumptions[:]

    #find the start index for 'i'
    if isinstance(info.indicies, tuple):
        i_start = info.indicies[0]
        i_end = info.indicies[1]
    else:
        i_start = 0
        i_end = info.indicies.size

    #add to ranges
    iname_range.append('{}<={}<{}'.format(i_start, info.var_name, i_end))
    iname_range.append('{}<=j<{}'.format(0, test_size))

    if isinstance(test_size, str):
        assumptions.append('{0} > 0'.format(test_size))

    for iname, irange in info.extra_inames:
        inames.append(iname)
        iname_range.append(irange)

    #construct the kernel args
    pre_instructions = [pre_inst[k] if k in pre_inst else k
                            for k in info.pre_instructions]

    post_instructions = info.post_instructions[:]

    def subs_preprocess(key, value):
        #find the instance of ${key} in kernel_str
        result = __find_indent(skeleton, key, value)
        return Template(result).safe_substitute(var_name=info.var_name)

    kernel_str = Template(skeleton).safe_substitute(
        var_name=info.var_name,
        pre=subs_preprocess('${pre}', '\n'.join(pre_instructions)),
        post=subs_preprocess('${post}', '\n'.join(post_instructions)),
        main=subs_preprocess('${main}', '\n'.join(instructions)))

    #finally do extra subs
    if info.extra_subs:
        kernel_str = Template(kernel_str).safe_substitute(
            **info.extra_subs)

    iname_arr = []
    #generate iname strings
    for iname, irange in zip(*(inames,iname_range)):
        iname_arr.append(Template(
            '{[${iname}]:${irange}}').safe_substitute(
            iname=iname,
            irange=irange
            ))

    #make the kernel
    knl = lp.make_kernel(iname_arr,
        kernel_str,
        kernel_data=info.kernel_data,
        name='rateconst_' + info.name,
        target=target,
        assumptions=' and '.join(assumptions)
    )
    #fix parameters
    if info.parameters:
        knl = lp.fix_parameters(knl, **info.parameters)
    #prioritize and return
    knl = lp.prioritize_loops(knl, inames)
    return knl