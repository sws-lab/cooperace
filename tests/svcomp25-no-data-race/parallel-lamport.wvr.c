// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "parallel-lamport.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

typedef unsigned int size_t;
extern void *malloc (size_t __size) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__malloc__)) ;

extern int   __VERIFIER_nondet_int(void);
extern _Bool __VERIFIER_nondet_bool(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

_Atomic _Bool e1, e2;
_Atomic int n1, n2, i, j, i1, i2, n;
int* f;

int *create_fresh_int_array(int size);

void* thread1(void* _argptr) {
  e1 = 1;
  n1 = n2 + 1;
  e1 = 0;
  assume_abort_if_not( !e2 );
  int tmp1 = n1;
  int tmp2 = n2;
  assume_abort_if_not( ( tmp2 == 0 ) || ( tmp2 >= tmp1 ) );
  i1 = i;
  i = f[i1];
  int tmpI = i;
  int tmpN = n;
  assume_abort_if_not(0 <= tmpI && tmpI < tmpN);
  n1 = 0;

  return 0;
}

void* thread2(void* _argptr) {
  e2 = 1;
  n2 = n1 + 1;
  e2 = 0;
  assume_abort_if_not( !e1 );
  int tmp1 = n1;
  int tmp2 = n2;
  assume_abort_if_not( ( tmp2 == 0 ) || ( tmp2 >= tmp1 ) );
  assume_abort_if_not ( ( tmp1 == 0 ) || ( tmp1 > tmp2 ) );
  i2 = i;
  i = f[i2];
  int tmpI = i;
  int tmpN = n;
  assume_abort_if_not(0 <= tmpI && tmpI < tmpN);
  n2 = 0;

  return 0;
}

void* thread3(void* _argptr) {
  j = f[j];
  int tmpJ = j;
  int tmpN = n;
  assume_abort_if_not(0 <= tmpJ && tmpJ < tmpN);
  j = f[j];

  return 0;
}

int main() {
  pthread_t t1, t2, t3;

  // initialize global variables
  i  = __VERIFIER_nondet_int();
  j  = __VERIFIER_nondet_int();
  i1 = __VERIFIER_nondet_int();
  i2 = __VERIFIER_nondet_int();
  n  = __VERIFIER_nondet_int();
  f  = create_fresh_int_array(n);

  // main method
  assume_abort_if_not( i == j );
  assume_abort_if_not( 0 <= i && i < n );

  e1 = 0;
  e2 = 0;
  n1 = 0;
  n2 = 0;

  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_create(&t3, 0, thread3, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  pthread_join(t3, 0);

  assume_abort_if_not( !( i == j ) );
  reach_error();

  return 0;
}

int *create_fresh_int_array(int size) {
  assume_abort_if_not(size >= 0);
  assume_abort_if_not(size <= (((size_t) 4294967295) / sizeof(int)));

  int* arr = (int*)malloc(sizeof(int) * (size_t)size);
  for (int i = 0; i < size; i++) {
    arr[i] = __VERIFIER_nondet_int();
  }
  return arr;
}
